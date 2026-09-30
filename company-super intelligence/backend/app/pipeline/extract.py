"""Steps 9–13: event extraction, cross-document dedup/clustering, verification and materiality.

Rules run always. If a cheap LLM is configured it refines type/summary/guidance per document, but
every LLM claim must carry a verbatim evidence quote that we locate in the source text — otherwise
the LLM output for that document is discarded and the rules result is kept.
"""
import hashlib
import json
import logging
import re
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models as m
from ..llm import LLMError, get_llm
from .taxonomy import BY_KEY, EVENT_TYPE_KEYS, classify_title, direction

log = logging.getLogger(__name__)

STOP = set("the a an of to in on for and or with by at from is are was were its it as has have ltd limited inc plc co company share shares stock".split())
NUMERIC = re.compile(r"(₹|rs\.?|\$|£|€|usd|inr)?\s?\d[\d,.]*\s?(crore|cr|lakh|million|mn|billion|bn|%|percent)", re.I)


def tokens(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", s.lower()) if w not in STOP and len(w) > 2}


def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def claim_type_for(tier: int, doc_type: str) -> str:
    if tier == 1:
        return "fact"
    if tier == 2:
        return "company_claim"
    if tier in (3, 4):
        return "media_report"
    if tier == 5:
        return "company_social"
    return "unverified"


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip().lower()


# ---------------------------------------------------------------- LLM refinement
LLM_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["documents"],
    "properties": {"documents": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["doc_ref", "about_company", "event_type", "summary", "evidence_quote", "sentiment", "forward_looking"],
        "properties": {
            "doc_ref": {"type": "integer"},
            "about_company": {"type": "boolean"},
            "event_type": {"type": "string", "enum": EVENT_TYPE_KEYS},
            "summary": {"type": "string"},
            "evidence_quote": {"type": "string"},
            "sentiment": {"type": "string", "enum": ["positive", "negative", "neutral", "mixed"]},
            "forward_looking": {"type": "array", "items": {
                "type": "object", "additionalProperties": False,
                "required": ["quote", "speaker", "metric", "target", "deadline"],
                "properties": {"quote": {"type": "string"}, "speaker": {"type": "string"}, "metric": {"type": "string"},
                               "target": {"type": "string"}, "deadline": {"type": "string"}},
            }},
        },
    }}},
}

LLM_SYSTEM = """You extract structured company events from source documents for a financial-intelligence database.
Rules:
- Use ONLY the text provided for each document. Never add facts, numbers, dates, customers or names that are not in that document's text.
- about_company: false if the document is not actually about the named company (e.g. a different company with a similar name).
- summary: <= 35 words, neutral, attributes claims ("The company said...", "<publisher> reported...").
- evidence_quote: an exact, verbatim substring (10-200 chars) copied from the document text that supports the summary.
- forward_looking: only explicit management targets/expectations quoted in the document (empty list if none). quote must be verbatim.
- sentiment is the direction for the company's business, not stock-price advice."""


def llm_refine(company: m.Company, docs: list[m.Document]) -> dict[str, dict]:
    llm = get_llm("cheap")
    if not llm or not docs:
        return {}
    out: dict[str, dict] = {}
    for start in range(0, len(docs), 15):
        batch = docs[start:start + 15]
        payload = []
        for i, d in enumerate(batch):
            text = f"{d.title}\n{d.snippet or ''}\n{(d.body_text or '')[:2500]}"
            payload.append({"doc_ref": i, "published": d.published_at.date().isoformat() if d.published_at else None, "text": text})
        user = f"Company: {company.legal_name} ({company.country_code}, {company.industry_text or 'industry unknown'})\n\nDocuments:\n{json.dumps(payload, ensure_ascii=False)}"
        try:
            res = llm.json(LLM_SYSTEM, user, LLM_SCHEMA, max_tokens=6000)
        except (LLMError, ValueError) as e:
            log.warning("LLM extraction failed, using rules: %s", e)
            continue
        for item in res.get("documents", []):
            ref = item.get("doc_ref")
            if not isinstance(ref, int) or not (0 <= ref < len(batch)):
                continue
            d = batch[ref]
            full = _norm(f"{d.title}\n{d.snippet or ''}\n{(d.body_text or '')[:2500]}")
            quote = _norm(item.get("evidence_quote", ""))
            if len(quote) < 10 or quote not in full:
                continue  # unverifiable -> discard LLM output for this doc
            item["forward_looking"] = [f for f in item.get("forward_looking", []) if len(_norm(f["quote"])) >= 10 and _norm(f["quote"]) in full]
            item["_llm"] = llm.name
            out[d.id] = item
    return out


# ---------------------------------------------------------------- rule-based guidance detection
FWD = re.compile(r"[^.]*\b(expects?|expected|targets?|aims?|plans? to|guidance|anticipates?|intends? to|will (reach|achieve|commission|complete|double))\b[^.]*\b(by|in|during|for)\s+(fy ?\d{2,4}(?:\s?[–\-/]\s?\d{2,4})?|q[1-4] ?(?:fy)?\s?\d{2,4}|20\d\d(?:\s?[–\-]\s?\d{2,4})?|next (?:year|quarter)|h[12] ?\d{2,4})[^.]*\.", re.I)
METRICS = [("capacity", r"capacity"), ("revenue", r"revenue|sales|turnover"), ("margin", r"margin"),
           ("plant_commissioning", r"plant|facility|commission"), ("debt", r"debt"), ("capex", r"capex|capital expenditure"),
           ("order_book", r"order book|orders"), ("exports", r"export"),
           ("distribution_network", r"dealership|showroom|distribution|network|outlets|touchpoints"), ("volumes", r"units|volumes|deliveries")]


def rule_guidance(d: m.Document) -> list[dict]:
    if d.credibility_tier > 2 or not d.body_text:
        return []
    out = []
    for mt in FWD.finditer(d.body_text[:20000]):
        sent = mt.group(0).strip()
        if len(sent) > 400:
            continue
        metric = next((k for k, rx in METRICS if re.search(rx, sent, re.I)), "other")
        tgt = re.search(r"(?:₹|rs\.?|\$|£)?\s?\d[\d,.]*\s?(?:crore|cr|lakh|million|billion|%|units|dealerships|showrooms|plants?|mw|gw)?", sent, re.I)
        out.append({"quote": sent, "speaker": "", "metric": metric, "target": tgt.group(0).strip() if tgt else "", "deadline": mt.group(4)})
    return out[:5]


# ---------------------------------------------------------------- main
def score_materiality(event_type: str, best_tier: int, n_domains: int, text: str) -> tuple[float, str]:
    base = BY_KEY.get(event_type, BY_KEY["other"]).base
    tier_bonus = {1: 0.25, 2: 0.2, 3: 0.15, 4: 0.1, 5: 0.02, 6: 0.0}.get(best_tier, 0)
    s = base * 0.65 + tier_bonus + (0.08 if n_domains >= 2 else 0) + (0.05 if NUMERIC.search(text) else 0)
    s = round(min(1.0, s), 3)
    return s, "high" if s >= 0.62 else "medium" if s >= 0.42 else "low"


def verification_for(tiers: list[int], domains: set[str]) -> str:
    if 1 in tiers:
        return "verified_primary"
    if 2 in tiers:
        return "company_claim"
    media = [t for t in tiers if t in (3, 4)]
    if media and len(domains) >= 2:
        return "corroborated"
    if media:
        return "single_source"
    return "unverified"


def extract_events(db: Session, company: m.Company, lookback_days: int, progress=None) -> dict:
    docs = db.execute(select(m.Document).where(m.Document.company_id == company.id, m.Document.processed.is_(False))).scalars().all()
    refined = llm_refine(company, docs)
    if progress:
        progress(f"extracting events from {len(docs)} new documents" + (f" (LLM-verified: {len(refined)})" if refined else " (rules)"))

    since = date.today() - timedelta(days=max(lookback_days, 30) + 5)
    existing = db.execute(select(m.Event).where(m.Event.company_id == company.id, m.Event.event_date >= since)).scalars().all()
    created, merged, dropped, guidance_n = 0, 0, 0, 0

    for d in docs:
        d.processed = True
        r = refined.get(d.id)
        if r and not r["about_company"]:
            d.match_confidence = 0.1
            dropped += 1
            continue
        # Classify on the headline when it is informative; snippets often carry boilerplate (letterheads, addresses).
        text = d.title if len(d.title) >= 30 else f"{d.title} {d.snippet or ''}"
        hint = (d.raw or {}).get("event_type_hint")
        if r:
            etype, conf, summary, sentiment, quote, by = r["event_type"], 0.85, r["summary"], r["sentiment"], r["evidence_quote"], r["_llm"]
        else:
            etype, conf = (hint, 0.9) if hint and hint != "other" else classify_title(text)
            summary, sentiment, quote, by = None, direction(text, etype), None, "rules"
        ev_date = d.published_at.date() if d.published_at else date.today()
        tk = tokens(d.title)
        # Cluster with an existing event: same/similar story within +-3 days
        match = None
        for e in existing:
            if e.event_date and abs((e.event_date - ev_date).days) <= 3:
                sim = jaccard(tk, tokens(e.title))
                if (e.event_type == etype and sim >= 0.45) or sim >= 0.7:
                    match = e
                    break
        if match:
            if not db.execute(select(m.EventSource).where(m.EventSource.event_id == match.id, m.EventSource.document_id == d.id)).first():
                db.add(m.EventSource(event_id=match.id, document_id=d.id, role="supporting"))
            if d.credibility_tier < min((es.document.credibility_tier for es in match.sources if es.document), default=9):
                match.title = d.title  # prefer the more authoritative headline
            merged += 1
            ev = match
        else:
            ev = m.Event(
                company_id=company.id, event_type=etype, category=BY_KEY.get(etype, BY_KEY["other"]).category,
                title=d.title, description=summary, event_date=ev_date, announcement_date=ev_date,
                materiality="low", confidence=conf, verification="unverified", sentiment=sentiment,
                fingerprint=hashlib.sha256(f"{company.id}|{etype}|{ev_date}|{' '.join(sorted(tk))}".encode()).hexdigest(),
                extracted_by=by, evidence_quote=quote, claim_type=claim_type_for(d.credibility_tier, d.doc_type),
            )
            db.add(ev)
            db.flush()
            db.add(m.EventSource(event_id=ev.id, document_id=d.id, role="primary"))
            existing.append(ev)
            created += 1
        db.flush()

        # Management statements / guidance (company or regulatory documents only)
        fl = (r or {}).get("forward_looking") or rule_guidance(d)
        if d.credibility_tier <= 2 or d.doc_type == "filing":
            for f in fl:
                if db.execute(select(m.ManagementStatement).where(m.ManagementStatement.company_id == company.id, m.ManagementStatement.statement == f["quote"])).first():
                    continue
                st = m.ManagementStatement(company_id=company.id, document_id=d.id, speaker=f.get("speaker") or None,
                                           statement=f["quote"], statement_date=ev_date, is_forward_looking=True, topic=f.get("metric"))
                db.add(st)
                db.flush()
                db.add(m.ManagementGuidance(company_id=company.id, statement_id=st.id, metric=f.get("metric") or "other",
                                            target_value=f.get("target") or None, deadline_text=f.get("deadline") or None, given_on=ev_date))
                guidance_n += 1

    # Recompute verification + materiality for all touched events
    for e in existing:
        srcs = db.execute(select(m.Document).join(m.EventSource, m.EventSource.document_id == m.Document.id).where(m.EventSource.event_id == e.id)).scalars().all()
        if not srcs:
            continue
        tiers = [s.credibility_tier for s in srcs]
        domains = {re.sub(r"^www\.", "", (s.url.split("/")[2] if "://" in s.url else s.url)) for s in srcs}
        e.verification = verification_for(tiers, domains)
        best = min(tiers)
        e.claim_type = claim_type_for(best, srcs[0].doc_type)
        if e.verification == "corroborated":
            e.claim_type = "media_report"
        e.materiality_score, e.materiality = score_materiality(e.event_type, best, len(domains), " ".join(s.title for s in srcs))
    db.flush()
    return {"events_created": created, "events_merged": merged, "docs_rejected_by_llm": dropped, "guidance_items": guidance_n}
