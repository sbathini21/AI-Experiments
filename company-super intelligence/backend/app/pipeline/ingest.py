"""Steps 3–10: run connectors, normalise, company-match, credibility-score and store documents (dedup by hash)."""
import hashlib
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models as m
from ..reference import SOURCE_REGISTRY, domain_of, lookup_tier
from ..sources.base import CompanyContext, ConnectorResult, RawDoc
from ..sources.company_ir import CompanyIR
from ..sources.connectors import ALL_CONNECTORS
from .resolve import industry_keywords, normalize_name

log = logging.getLogger(__name__)

TRACKING_PARAMS = re.compile(r"([?&])(utm_[^=&]+|fbclid|gclid|ref|cmpid)=[^&]*")


CONNECTORS = [*ALL_CONNECTORS, CompanyIR()]


def build_context(c: m.Company, db: Session | None = None) -> CompanyContext:
    from ..reference import EXCHANGES

    code = {e[0]: e[1] for e in EXCHANGES}
    short = c.short_name or normalize_name(c.legal_name).title()
    domains = []
    if c.website:
        domains.append(domain_of(c.website if "://" in c.website else "https://" + c.website))
    return CompanyContext(
        company_id=c.id, legal_name=c.legal_name, short_name=short, aliases=c.aliases or [],
        country_code=c.country_code, tickers=[(code.get(s.exchange_mic, s.exchange_mic), s.ticker) for s in c.securities],
        identifiers=c.identifiers or {}, industry=c.industry_text,
        industry_keywords=industry_keywords(c.industry_text), website_domains=domains, linkedin_url=c.linkedin_url,
        known_urls=set(db.execute(select(m.Document.url).where(m.Document.company_id == c.id)).scalars()) if db else set(),
    )


def canonical_url(url: str) -> str:
    u = TRACKING_PARAMS.sub(r"\1", url.strip()).rstrip("?&")
    return re.sub(r"^http://", "https://", u)


def content_hash(d: RawDoc) -> str:
    # Same story syndicated under different URLs collapses via normalised title + day.
    title = re.sub(r"[^a-z0-9 ]", "", d.title.lower())
    title = re.sub(r"\s+", " ", title).strip()
    day = d.published_at.date().isoformat() if d.published_at else ""
    basis = f"{d.filing_id}" if d.filing_id else f"{title}|{day}" if len(title) > 25 else canonical_url(d.url)
    return hashlib.sha256(basis.encode()).hexdigest()


def match_confidence(ctx: CompanyContext, d: RawDoc) -> float:
    """Is this document really about this company? Regulatory connectors keyed by identifier = 1.0."""
    if d.connector in ("sec_edgar", "companies_house", "india_exchange_announcements", "company_ir"):
        return 1.0
    text = normalize_name(f"{d.title} {d.snippet or ''} {(d.body_text or '')[:3000]}")
    names = {normalize_name(n) for n in [ctx.short_name, ctx.legal_name, *ctx.aliases] if n}
    names = {n for n in names if len(n) >= 4}
    if any(re.search(rf"\b{re.escape(n)}\b", text) for n in names):
        return 0.95
    tickers = [t for _, t in ctx.tickers if len(t) >= 3]
    if any(re.search(rf"\b{re.escape(t.lower())}\b", text) for t in tickers):
        return 0.6
    return 0.2


def _run_connector(conn, ctx, since) -> ConnectorResult:
    ok, note = conn.enabled()
    if not ok:
        return ConnectorResult(conn.name, [], "skipped", note)
    if not conn.applies(ctx):
        return ConnectorResult(conn.name, [], "skipped", "not applicable to this market/company")
    try:
        docs = conn.collect(ctx, since)
        return ConnectorResult(conn.name, docs, "ok", None)
    except Exception as e:
        log.warning("connector %s failed: %s", conn.name, e)
        return ConnectorResult(conn.name, [], "error", str(e)[:200])


def ensure_sources(db: Session) -> None:
    if db.execute(select(m.Source).limit(1)).first():
        return
    for dom, name, stype, tier, notes in SOURCE_REGISTRY:
        db.add(m.Source(domain=dom, name=name, source_type=stype, credibility_tier=tier, license_notes=notes))
    db.flush()


def _source_for(db: Session, url: str, ctx: CompanyContext, publisher: str | None) -> tuple[m.Source, int]:
    tier, stype, name = lookup_tier(url, ctx.website_domains)
    dom = domain_of(url)
    src = db.execute(select(m.Source).where(m.Source.domain == dom)).scalar_one_or_none()
    if not src:
        # Longest registered suffix match (e.g. auto.economictimes.indiatimes.com)
        for s in db.execute(select(m.Source)).scalars():
            if s.domain and dom.endswith("." + s.domain):
                return s, s.credibility_tier if stype != "company" else 2
        src = m.Source(domain=dom, name=publisher or name, source_type=stype, credibility_tier=tier)
        db.add(src)
        db.flush()
    return src, (2 if stype == "company" else src.credibility_tier)


def collect_and_store(db: Session, c: m.Company, since: datetime, progress=None) -> dict:
    ctx = build_context(c, db)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda conn: _run_connector(conn, ctx, since), CONNECTORS))
    ensure_sources(db)  # after network I/O so the write transaction stays short
    coverage, new_docs, dup, off_topic = [], [], 0, 0
    for res in results:
        coverage.append({"connector": res.name, "status": res.status, "note": res.note, "found": len(res.docs)})
        if progress:
            progress(f"{res.name}: {res.status} ({len(res.docs)} items)" + (f" — {res.note}" if res.note else ""))
        for d in res.docs:
            if not d.url or not d.title:
                continue
            if d.published_at and d.published_at < since:
                continue
            conf = match_confidence(ctx, d)
            if conf < 0.5:
                off_topic += 1
                continue
            h = content_hash(d)
            existing = db.execute(select(m.Document).where(m.Document.company_id == c.id, m.Document.content_hash == h)).scalar_one_or_none()
            if existing:
                dup += 1
                continue
            src, tier = _source_for(db, d.url, ctx, d.publisher)
            if d.connector == "sec_edgar" or d.connector == "companies_house":
                tier = 1
            doc = m.Document(
                company_id=c.id, source_id=src.id, doc_type=d.doc_type, connector=d.connector, url=canonical_url(d.url),
                title=d.title[:1000], snippet=d.snippet, body_text=d.body_text, author=d.author, language=d.language,
                published_at=d.published_at, content_hash=h, credibility_tier=tier, match_confidence=conf,
                raw={**(d.raw or {}), "event_type_hint": d.event_type_hint, "publisher": d.publisher, "items": d.items},
            )
            db.add(doc)
            db.flush()
            if d.doc_type == "filing":
                db.add(m.Filing(document_id=doc.id, regulator=d.regulator or "", form_type=d.form_type, filing_id=d.filing_id, items=d.items))
            elif d.doc_type == "article":
                db.add(m.Article(document_id=doc.id, publisher=d.publisher))
            new_docs.append(doc)
    db.flush()
    return {"coverage": coverage, "new_documents": len(new_docs), "duplicates_skipped": dup, "off_topic_dropped": off_topic}
