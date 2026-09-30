"""Entity resolution: free text -> exactly one listed company, or an explicit list of candidates.

Order: local securities master (seeded from SEC / NSE / BSE / LSE lists) -> OpenFIGI search.
Never silently picks between similar names: auto-select only when the best match is strong AND
clearly ahead of the runner-up.
"""
import logging
import re
from datetime import datetime, timezone

from rapidfuzz import fuzz
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .. import models as m
from ..reference import EXCHANGE_BY_FIGI, EXCHANGES, FIGI_COMPOSITE
from ..sources.http import fetch

log = logging.getLogger(__name__)

SUFFIXES = re.compile(r"\b(limited|ltd|plc|inc|incorporated|corp|corporation|co|company|holdings?|group|llc|sa|ag|nv)\b\.?", re.I)
LEADING_NOISE = [re.compile(p, re.I) for p in [
    r"^(please|hey|ok|okay)\b[, ]*",
    r"^(can you |could you )?(give|show|tell|get|send)( me)?\b",
    r"^(what|why|how)( is| are|'s| was| has| have)?( been)?\b",
    r"^(should i know|do i need to know|changed|happened|happening|going on)\b",
    r"^(today'?s|the|a|an|latest|recent|quick|brief|briefing|update|updates|news|developments?|summary|report)\b",
    r"^(on|at|in|for|with|about|regarding|of|to)\b",
]]
TRAILING_NOISE = re.compile(
    r"\s+(today|yesterday|this week|this month|this quarter|this year|in the news( today)?|lately|recently|now|"
    r"update|updates|news|latest developments|developments|stock|shares|share price)\??$", re.I)

WINDOWS = [
    (re.compile(r"\b(today|24 ?h(ours)?|yesterday)\b", re.I), 1),
    (re.compile(r"\b(this week|7 ?d(ays)?|past week|last week)\b", re.I), 7),
    (re.compile(r"\b(this month|30 ?d(ays)?|past month|last month)\b", re.I), 30),
    (re.compile(r"\b(this quarter|90 ?d(ays)?|last quarter)\b", re.I), 90),
    (re.compile(r"\b(this year|1 ?y(ear)?|past year|last year)\b", re.I), 365),
]


def normalize_name(s: str) -> str:
    s = SUFFIXES.sub(" ", s.lower())
    return re.sub(r"[^a-z0-9& ]+", " ", re.sub(r"\s+", " ", s)).strip()


def parse_query(text: str) -> dict:
    """'Give me today's update on Kinetic Engineering.' -> {'company': 'Kinetic Engineering', 'window_days': 1}"""
    raw = text.strip().rstrip(".?!")
    window = 7
    for rx, days in WINDOWS:
        if rx.search(raw):
            window = days
            break
    q = raw
    for _ in range(12):
        prev = q
        for rx in LEADING_NOISE:
            q = rx.sub("", q).strip(" ,?.'\"")
        q = TRAILING_NOISE.sub("", q).strip(" ,?.'\"")
        if q == prev:
            break
    q = re.sub(r"'s$", "", q)
    return {"company": q or raw, "window_days": window, "raw": text}


def _name_score(nq: str, name: str) -> float:
    nn = normalize_name(name)
    if not nn:
        return 0
    if nq == nn:
        return 100
    qt, nt = nq.split(), nn.split()
    if qt and all(t in nt for t in qt):
        # every query word present as a whole word: rank by how much of the name the query covers
        return 80 + 15 * len(qt) / len(nt)
    return fuzz.token_sort_ratio(nq, nn) * 0.9


def _score(query: str, c: m.Company, tickers: list[str]) -> float:
    nq = normalize_name(query)
    names = [c.legal_name, c.short_name or "", *(c.aliases or [])]
    best = max((_name_score(nq, n) for n in names if n), default=0)
    if any(query.strip().upper() == t.upper() for t in tickers):
        best = max(best, 99)
    if c.securities and all(x.exchange_mic == "OTCM" for x in c.securities):
        best -= 3  # prefer main-board listings over OTC-only names on near ties
    return best


def _candidate_from_company(c: m.Company, score: float) -> dict:
    secs = sorted(c.securities, key=lambda s: not s.is_primary)
    exch = {e[0]: e[1] for e in EXCHANGES}
    return {
        "candidate_id": f"company:{c.id}",
        "company_id": c.id,
        "name": c.legal_name,
        "country": c.country_code,
        "listings": [{"exchange": exch.get(s.exchange_mic, s.exchange_mic), "ticker": s.ticker, "isin": s.isin} for s in secs],
        "industry": c.industry_text,
        "score": round(score, 1),
        "source": "local",
    }


def search_local(db: Session, query: str, limit: int = 8) -> list[dict]:
    nq = normalize_name(query)
    tokens = [t for t in nq.split() if len(t) > 1][:3]
    if not tokens:
        return []
    conds = [func.lower(m.Company.legal_name).like(f"%{t}%") for t in tokens]
    conds.append(func.upper(m.Security.ticker) == query.strip().upper())
    rows = db.execute(
        select(m.Company).outerjoin(m.Security).where(or_(*conds)).distinct().limit(300)
    ).scalars().all()
    scored = []
    for c in rows:
        s = _score(query, c, [x.ticker for x in c.securities])
        if s >= 60:
            scored.append((s, c))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [_candidate_from_company(c, s) for s, c in scored[:limit]]


def search_openfigi(query: str, limit: int = 8) -> list[dict]:
    from ..config import get_settings

    headers = {"Content-Type": "application/json"}
    if get_settings().openfigi_api_key:
        headers["X-OPENFIGI-APIKEY"] = get_settings().openfigi_api_key
    r = fetch("https://api.openfigi.com/v3/search", method="POST", headers=headers, ttl=86400,
              json_body={"query": query, "securityType2": "Common Stock", "marketSecDes": "Equity"})
    if r.status_code != 200:
        log.warning("OpenFIGI search failed: %s", r.status_code)
        return []
    grouped: dict[str, dict] = {}
    for row in r.json().get("data", []):
        code = row.get("exchCode")
        mic = EXCHANGE_BY_FIGI.get(code)
        country = next((e[3] for e in EXCHANGES if e[0] == mic), None) or FIGI_COMPOSITE.get(code)
        if not country:
            continue  # outside supported markets (architecture supports more; add rows to reference.py)
        key = row.get("shareClassFIGI") or row["figi"]
        g = grouped.setdefault(key, {
            "candidate_id": f"figi:{row.get('compositeFIGI') or row['figi']}",
            "company_id": None, "name": row["name"].title().replace("Ltd", "Ltd"), "country": country,
            "listings": [], "industry": None, "source": "openfigi",
            "score": round(_name_score(normalize_name(query), row["name"]), 1),
            "_rows": [],
        })
        g["_rows"].append(row)
        if mic:
            g["listings"].append({"exchange": next(e[1] for e in EXCHANGES if e[0] == mic), "ticker": row["ticker"], "isin": None})
    out = [g for g in grouped.values() if g["listings"] and g["score"] >= 55]
    out.sort(key=lambda g: g["score"], reverse=True)
    return out[:limit]


def resolve(db: Session, text: str) -> dict:
    parsed = parse_query(text)
    q = parsed["company"]
    cands = search_local(db, q)
    top_s = cands[0]["score"] if cands else 0
    runner_s = cands[1]["score"] if len(cands) > 1 else 0
    if top_s < 90 or runner_s >= 87:
        seen = {c["name"].lower() for c in cands}
        cands += [c for c in search_openfigi(q) if c["name"].lower() not in seen]
        cands.sort(key=lambda c: c["score"], reverse=True)
    # Materialize OpenFIGI hits so every candidate has a stable company_id (grows the securities master).
    for c in cands:
        if not c.get("company_id") and c.get("_rows"):
            co = materialize_figi(db, c)
            c["company_id"], c["candidate_id"] = co.id, f"company:{co.id}"
    db.commit()
    status = "not_found"
    selected = None
    if cands:
        top = cands[0]
        runner = cands[1]["score"] if len(cands) > 1 else 0
        # Auto-select only a strong, clearly-separated match; otherwise ask the user.
        if top["score"] >= 90 and runner < 87 and top["score"] - runner >= 10:
            status, selected = "resolved", top
        else:
            status = "ambiguous"
    public = [{k: v for k, v in c.items() if not k.startswith("_")} for c in cands]
    return {"status": status, "query": parsed, "selected": selected and {k: v for k, v in selected.items() if not k.startswith("_")},
            "candidates": public}


# ---------------------------------------------------------------- materialize & enrich
def materialize_figi(db: Session, cand: dict) -> m.Company:
    """Create company + securities rows from an OpenFIGI candidate (re-uses existing if present)."""
    rows = cand.get("_rows") or []
    for row in rows:
        mic = EXCHANGE_BY_FIGI.get(row.get("exchCode"))
        if mic:
            sec = db.execute(select(m.Security).where(m.Security.exchange_mic == mic, m.Security.ticker == row["ticker"])).scalar_one_or_none()
            if sec:
                return sec.company
    nm = normalize_name(rows[0]["name"] if rows else cand["name"])
    for existing in db.execute(select(m.Company).where(func.lower(m.Company.legal_name).like(f"%{nm.split()[0]}%"))).scalars():
        if normalize_name(existing.legal_name) == nm:
            for row in rows:
                mic = EXCHANGE_BY_FIGI.get(row.get("exchCode"))
                if mic and not any(x.exchange_mic == mic for x in existing.securities):
                    db.add(m.Security(company=existing, exchange_mic=mic, ticker=row["ticker"], figi=row["figi"]))
                    home = next(e[3] for e in EXCHANGES if e[0] == mic)
                    if existing.country_code == "US" and home != "US" and re.search(r"\b(plc|ltd|limited)\b", existing.legal_name, re.I):
                        existing.country_code = home  # US-listed ADR of a foreign issuer: home market wins
            db.flush()
            return existing
    c = m.Company(legal_name=rows[0]["name"].title() if rows else cand["name"], short_name=None,
                  country_code=cand["country"], identifiers={"composite_figi": rows[0].get("compositeFIGI") if rows else None})
    c.short_name = normalize_name(c.legal_name).title()
    db.add(c)
    first = True
    for row in rows:
        mic = EXCHANGE_BY_FIGI.get(row.get("exchCode"))
        if not mic:
            continue
        db.add(m.Security(company=c, exchange_mic=mic, ticker=row["ticker"], figi=row["figi"], is_primary=first))
        first = False
    db.flush()
    return c


def _wiki_headers() -> dict:
    # Wikimedia's User-Agent policy requires a descriptive agent with contact details.
    from ..config import get_settings
    return {"User-Agent": f"CompanyIntel/0.1 ({get_settings().sec_user_agent})"}


def enrich_profile(db: Session, c: m.Company, force: bool = False) -> m.Company:
    """Fill LEI/legal address (GLEIF), industry/website/ISIN (Wikidata, community-maintained),
    and SEC profile data. Every enrichment records where it came from in c.profile['provenance']."""
    if c.profile_refreshed_at and not force:
        age = datetime.now(timezone.utc) - c.profile_refreshed_at.replace(tzinfo=timezone.utc)
        if age.days < 7:
            return c
    profile = dict(c.profile or {})
    prov = dict(profile.get("provenance") or {})
    ids = dict(c.identifiers or {})

    # SEC (US): authoritative SIC industry + website + fiscal year end
    if ids.get("sec_cik"):
        r = fetch(f"https://data.sec.gov/submissions/CIK{str(ids['sec_cik']).zfill(10)}.json", ttl=900)
        if r.status_code == 200:
            d = r.json()
            c.industry_text = c.industry_text or d.get("sicDescription")
            if d.get("website"):
                c.website = c.website or d["website"]
            profile.update({"sic": d.get("sic"), "fiscal_year_end": d.get("fiscalYearEnd"),
                            "state_of_incorporation": d.get("stateOfIncorporation")})
            prov["industry"] = prov.get("industry") or "SEC EDGAR SIC code"

    # GLEIF (all markets): LEI + legal address, CC0
    try:
        r = fetch("https://api.gleif.org/api/v1/lei-records", ttl=86400, params={
            "filter[entity.legalName]": c.legal_name.upper().replace("LTD", "LIMITED"),
            "filter[entity.legalAddress.country]": c.country_code or "", "page[size]": 3})
        if r.status_code == 200 and r.json().get("data"):
            rec = r.json()["data"][0]
            c.lei = rec["id"]
            ent = rec["attributes"]["entity"]
            c.legal_name = ent["legalName"]["name"].title()
            addr = ent.get("legalAddress") or {}
            profile["legal_address"] = ", ".join(filter(None, [*addr.get("addressLines", []), addr.get("city"), addr.get("postalCode"), addr.get("country")]))
            prov["lei"] = "GLEIF"
    except Exception as e:  # enrichment is best-effort
        log.info("GLEIF enrichment failed: %s", e)

    # Wikidata (CC0, community-maintained -> lower confidence, labelled as such)
    try:
        r = fetch("https://www.wikidata.org/w/api.php", ttl=86400, headers=_wiki_headers(), params={
            "action": "wbsearchentities", "search": normalize_name(c.legal_name), "language": "en", "format": "json", "type": "item", "limit": 5})
        hits = r.json().get("search", []) if r.status_code == 200 else []
        best = next((h for h in hits if fuzz.token_sort_ratio(normalize_name(h.get("label", "")), normalize_name(c.legal_name)) >= 90
                     and re.search(r"compan|manufactur|bank|firm|conglomerate|corporation|group|maker|business|enterprise|provider|retailer|producer", h.get("description", ""), re.I)), None)
        if best:
            ent = fetch("https://www.wikidata.org/w/api.php", ttl=86400, headers=_wiki_headers(), params={
                "action": "wbgetentities", "ids": best["id"], "props": "claims|descriptions", "languages": "en", "format": "json"}).json()["entities"][best["id"]]
            claims = ent.get("claims", {})

            def vals(p):
                return [s["mainsnak"].get("datavalue", {}).get("value") for s in claims.get(p, []) if s["mainsnak"].get("datavalue")]
            profile["description"] = profile.get("description") or best.get("description")
            if vals("P856") and not c.website:
                c.website = vals("P856")[0]
                prov["website"] = "Wikidata (community-maintained)"
            isins = [v for v in vals("P946") if isinstance(v, str)]
            if isins:
                ids["isin"] = isins[0]
                for s in c.securities:
                    if not s.isin and isins[0][:2] == (c.country_code or ""):
                        s.isin = isins[0]
            ind_ids = [v["id"] for v in vals("P452") if isinstance(v, dict)]
            if ind_ids:
                lab = fetch("https://www.wikidata.org/w/api.php", ttl=86400, headers=_wiki_headers(), params={
                    "action": "wbgetentities", "ids": "|".join(ind_ids[:3]), "props": "labels", "languages": "en", "format": "json"}).json()
                labels = [e["labels"]["en"]["value"] for e in lab.get("entities", {}).values() if "en" in e.get("labels", {})]
                if labels and not c.industry_text:
                    c.industry_text = labels[0].title()
                    profile["industry_labels"] = labels
                    prov["industry"] = "Wikidata (community-maintained)"
            if not c.industry_text and best.get("description"):
                profile["industry_hint"] = best["description"]
            ids["wikidata"] = best["id"]
    except Exception as e:
        log.info("Wikidata enrichment failed: %s", e)

    if not c.industry_text and profile.get("industry_hint"):
        hint = profile["industry_hint"].lower()
        for word, ind in [("automotive", "Automotive"), ("auto", "Automotive"), ("pharma", "Pharmaceuticals"), ("bank", "Banking"),
                          ("semiconductor", "Semiconductors"), ("software", "Software"), ("energy", "Energy"), ("oil", "Energy"),
                          ("steel", "Metals & Mining"), ("cement", "Construction Materials"), ("insurance", "Insurance")]:
            if word in hint:
                c.industry_text = ind
                prov["industry"] = f"Derived from Wikidata description: '{profile['industry_hint']}'"
                break

    profile["provenance"] = prov
    c.profile = profile
    c.identifiers = ids
    c.industry_id = _industry_id(db, c.industry_text)
    c.profile_refreshed_at = datetime.now(timezone.utc)
    db.flush()
    return c


INDUSTRY_KEYWORDS = {
    "automotive": ["automotive", "auto components", "two-wheeler", "EV"],
    "semiconductor": ["semiconductor", "chip", "foundry"],
    "pharma": ["pharma", "drug", "USFDA"],
    "bank": ["banking", "RBI", "loan growth"],
    "software": ["software", "IT services", "cloud"],
    "energy": ["energy", "oil", "power"],
}


def industry_keywords(industry: str | None) -> list[str]:
    if not industry:
        return []
    low = industry.lower()
    for k, v in INDUSTRY_KEYWORDS.items():
        if k in low:
            return v
    return [industry]


def _industry_id(db: Session, name: str | None) -> str | None:
    if not name:
        return None
    ind = db.execute(select(m.Industry).where(m.Industry.name == name)).scalar_one_or_none()
    if not ind:
        ind = m.Industry(name=name, keywords=industry_keywords(name))
        db.add(ind)
        db.flush()
    return ind.id
