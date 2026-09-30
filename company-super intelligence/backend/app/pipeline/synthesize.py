"""Steps 14–20: historical comparison, report synthesis, citation validation and voice script.

Two synthesizers share one output contract (REPORT JSON):
  * rules   — deterministic templates over stored events (always available, zero cost)
  * LLM     — strong model writes the prose, but may only cite source refs we hand it; every item is
              validated (refs exist, every number appears in a cited source) and failing items are dropped.
"""
import json
import logging
import re
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models as m
from ..llm import LLMError, get_llm
from ..reference import EXCHANGES, TIER_LABELS, domain_of

log = logging.getLogger(__name__)

DISCLAIMER = ("Informational research summary compiled from public sources. Not investment advice, a recommendation, "
              "or an offer to buy or sell securities. Verify material facts in the cited primary sources.")

MODES = {
    # category weight multipliers + number of summary points
    "quick": ({}, 7),
    "trader": ({"market": 1.6, "capital": 1.3, "regulatory": 1.3, "financial": 1.2, "ownership": 1.3}, 8),
    "long_term": ({"financial": 1.3, "capex": 1.4, "business": 1.2, "governance": 1.2, "market": 0.5}, 8),
    "research": ({}, 10),
    "institutional": ({"financial": 1.3, "capital": 1.3, "ownership": 1.4, "regulatory": 1.3, "governance": 1.2}, 10),
}

SECTION_TITLES = {
    "major_developments": "Major developments",
    "financial_developments": "Financial developments",
    "management_updates": "Management update",
    "industry_developments": "Industry development",
    "market_activity": "Market activity",
    "progress": "Where the company is getting ahead",
    "risks": "Where it is falling behind / risks",
    "management_execution": "Management execution",
    "watch_next": "What to watch next",
}

CLAIM_PREFIX = {
    "fact": "",
    "company_claim": "The company stated: ",
    "media_report": "",
    "company_social": "Company social-media post (not independently verified): ",
    "unverified": "Unverified: ",
}


def fmt_date(d: date | None) -> str:
    return d.strftime("%d %b %Y") if d else "undated"


def _sources_for_events(db: Session, events: list[m.Event]) -> tuple[list[dict], dict[str, list[int]], dict[int, m.Document]]:
    """Assign stable [n] numbers to documents cited by events. Returns (sources, event_id->refs, n->doc)."""
    sources, ev_refs, by_n, doc_n = [], {}, {}, {}
    for e in events:
        docs = db.execute(select(m.Document).join(m.EventSource, m.EventSource.document_id == m.Document.id)
                          .where(m.EventSource.event_id == e.id).order_by(m.Document.credibility_tier)).scalars().all()
        refs = []
        for d in docs[:4]:
            if d.id not in doc_n:
                n = len(sources) + 1
                doc_n[d.id] = n
                by_n[n] = d
                pub = (d.raw or {}).get("publisher") or domain_of(d.url)
                sources.append({
                    "ref": n, "document_id": d.id, "title": d.title, "url": d.url, "publisher": pub,
                    "tier": d.credibility_tier, "tier_label": TIER_LABELS.get(d.credibility_tier),
                    "doc_type": d.doc_type, "published_at": d.published_at.isoformat() if d.published_at else None,
                    "exchange_filing_copy": bool((d.raw or {}).get("exchange_filing_copy")),
                })
            refs.append(doc_n[d.id])
        ev_refs[e.id] = refs
    return sources, ev_refs, by_n


def _publisher(src: dict) -> str:
    return src["publisher"] or "A publication"


def _event_sentence(e: m.Event, srcs: list[dict]) -> str:
    lead = srcs[0] if srcs else None
    title = e.title.rstrip(".")
    if e.claim_type == "company_claim" and lead and lead.get("exchange_filing_copy"):
        body = f"In a disclosure to the stock exchange dated {fmt_date(e.event_date)} (copy on the company website), the company announced: \u201c{title}\u201d."
    elif e.claim_type == "fact" and lead:
        body = f"{title} ({lead['publisher']} filing, {fmt_date(e.event_date)})."
    elif e.claim_type == "media_report" and lead:
        more = f" and {len(srcs) - 1} other outlet{'s' if len(srcs) > 2 else ''}" if len(srcs) > 1 else ""
        body = f"{_publisher(lead)}{more} reported on {fmt_date(e.event_date)}: “{title}”."
    else:
        body = f"{CLAIM_PREFIX.get(e.claim_type, '')}“{title}” ({fmt_date(e.event_date)})."
    if e.description:
        body += f" {e.description}"
    return body


def _item(e: m.Event, refs: list[int], srcs_by_ref: dict[int, dict], new_ids: set[str]) -> dict:
    srcs = [srcs_by_ref[r] for r in refs]
    return {
        "text": _event_sentence(e, srcs), "claim_type": e.claim_type, "date": e.event_date.isoformat() if e.event_date else None,
        "event_ids": [e.id], "source_refs": refs, "materiality": e.materiality, "sentiment": e.sentiment,
        "event_type": e.event_type, "verification": e.verification, "is_new": e.id in new_ids,
    }


def _guidance_items(db: Session, company: m.Company) -> list[dict]:
    rows = db.execute(select(m.ManagementGuidance, m.ManagementStatement).join(
        m.ManagementStatement, m.ManagementGuidance.statement_id == m.ManagementStatement.id, isouter=True)
        .where(m.ManagementGuidance.company_id == company.id).order_by(m.ManagementGuidance.given_on.desc())).all()
    out = []
    for g, st in rows[:20]:
        track = db.execute(select(m.GuidanceTracking).where(m.GuidanceTracking.guidance_id == g.id).order_by(m.GuidanceTracking.assessed_at.desc())).scalars().first()
        doc = db.get(m.Document, st.document_id) if st and st.document_id else None
        out.append({
            "guidance_id": g.id, "statement": st.statement if st else None, "speaker": st.speaker if st else None,
            "given_on": g.given_on.isoformat() if g.given_on else None, "metric": g.metric, "target": g.target_value,
            "deadline": g.deadline_text, "status": g.status,
            "latest_evidence": track.rationale if track else "No later public evidence found yet.",
            "source": {"title": doc.title, "url": doc.url} if doc else None,
        })
    return out


def _rules_sections(events, ev_refs, srcs_by_ref, new_ids, company, guidance, weights) -> dict:
    def w(e):
        return e.materiality_score * weights.get(e.category, 1.0) + (0.15 if e.id in new_ids else 0)

    ranked = sorted(events, key=w, reverse=True)
    it = lambda e: _item(e, ev_refs[e.id], srcs_by_ref, new_ids)  # noqa: E731
    secs = {
        "major_developments": [it(e) for e in ranked if e.materiality == "high"][:3],
        "financial_developments": [it(e) for e in ranked if e.category == "financial"][:3],
        "management_updates": [it(e) for e in ranked if e.category == "governance"][:3],
        "industry_developments": [it(e) for e in ranked if e.category == "industry"][:3],
        "market_activity": [it(e) for e in ranked if e.category in ("market", "ownership")][:3],
        # "Getting ahead" needs evidence: primary/corroborated facts, or formal exchange disclosures of completed
        # actions (Reg 30-type filings). Plain management claims are excluded.
        # Raising capital or paying dividends is not operating progress, so capital/ownership/market events are excluded.
        "progress": [it(e) for e in ranked if e.sentiment == "positive" and e.category not in ("market", "capital", "ownership")
                     and (e.verification in ("verified_primary", "corroborated")
                          or (e.verification == "company_claim" and any(srcs_by_ref[r].get("exchange_filing_copy") for r in ev_refs[e.id])))][:4],
        "risks": [it(e) for e in ranked if e.sentiment in ("negative", "mixed") and e.category not in ("market",)][:4],
        "management_execution": [],
        "watch_next": [],
    }
    for g in guidance[:3]:
        secs["management_execution"].append({
            "text": f"Management guidance ({g['given_on'] or 'undated'}): “{(g['statement'] or '')[:220]}” — status: {g['status'].replace('_', ' ')}.",
            "claim_type": "company_claim", "date": g["given_on"], "event_ids": [], "source_refs": [], "materiality": "medium",
            "guidance_id": g["guidance_id"], "source_url": (g.get("source") or {}).get("url"),
        })
    # Watch next: deterministic, explicitly labelled as interpretation
    for e in ranked:
        if len(secs["watch_next"]) >= 3:
            break
        if e.verification in ("single_source", "unverified", "company_claim") and e.materiality in ("high", "medium"):
            if e.verification == "company_claim":
                label = {"capital": "use of proceeds and any further dilution or allotment disclosures",
                         "governance": "follow-up disclosures and their effect on execution",
                         "financial": "the next reported results against this",
                         "ownership": "subsequent shareholding disclosures"}.get(e.category, "evidence of execution in later results or disclosures")
            else:
                label = {"single_source": "confirmation from a primary or second independent source",
                         "unverified": "any official confirmation"}[e.verification]
            secs["watch_next"].append({"text": f"Watch for {label} regarding: “{e.title.rstrip('.')}”.",
                                       "claim_type": "ai_interpretation", "date": None, "event_ids": [e.id],
                                       "source_refs": ev_refs[e.id], "materiality": e.materiality})
    for g in guidance:
        if len(secs["watch_next"]) >= 4:
            break
        if g["deadline"]:
            secs["watch_next"].append({"text": f"Track progress against management's stated {g['metric'].replace('_', ' ')} target (deadline: {g['deadline']}).",
                                       "claim_type": "ai_interpretation", "date": None, "event_ids": [], "source_refs": [], "materiality": "medium"})
    summary = [it(e) for e in ranked if e.materiality in ("high", "medium")]
    return secs, summary


# ---------------------------------------------------------------- LLM synthesis + validation
ITEM_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["text", "claim_type", "source_refs", "event_ids"],
    "properties": {
        "text": {"type": "string"},
        "claim_type": {"type": "string", "enum": ["fact", "company_claim", "media_report", "company_social", "unverified", "ai_interpretation"]},
        "source_refs": {"type": "array", "items": {"type": "integer"}},
        "event_ids": {"type": "array", "items": {"type": "string"}},
    },
}
SYNTH_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["headline", "summary", *SECTION_TITLES.keys()],
    "properties": {"headline": {"type": "string"}, "summary": {"type": "array", "items": ITEM_SCHEMA},
                   **{k: {"type": "array", "items": ITEM_SCHEMA} for k in SECTION_TITLES}},
}
SYNTH_SYSTEM = """You are the synthesis layer of a company-intelligence product. You write a short daily briefing from a
provided list of structured events and numbered sources. You are not a search engine and you do not know anything else.

Hard rules:
1. Use only information in the provided events/sources. Never invent announcements, numbers, dates, customers, prices, or quotes.
2. Every item except claim_type "ai_interpretation" must cite >=1 source ref from the provided list; cite the refs that support it.
3. Keep categories separate and label them in the text: facts from filings; "The company stated..." for company claims;
   "<Publisher> reported..." for media; "Unverified:" for unverified/social. ai_interpretation items must be framed as
   possibilities ("could", "worth monitoring"), never as facts.
4. Each item is 1-3 sentences and includes the event date.
5. "progress" = evidence the company is getting ahead; "risks" = evidence it is falling behind. Evidence first; a management claim alone
   is not progress — say it is a claim. Empty arrays are fine; do not pad.
6. No buy/sell/hold language, price targets, or personalised advice.
7. If nothing material changed, headline must say: "No material new developments were identified in the latest public sources."
8. Prefer events marked is_new (new since the previous briefing); do not re-announce old news as new."""

NUM_RX = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _numbers_supported(text: str, corpus: str) -> bool:
    corpus_nums = {n.replace(",", "") for n in NUM_RX.findall(corpus)}
    for n in NUM_RX.findall(text):
        v = n.replace(",", "")
        if len(v) <= 1 or re.fullmatch(r"20\d\d|19\d\d", v):
            continue  # single digits and years are covered by the date check / ordinal usage
        if v not in corpus_nums:
            return False
    return True


def llm_sections(company, events, ev_refs, sources, by_n, new_ids, guidance, mode, window_days, prev_headline):
    llm = get_llm("strong")
    if not llm:
        return None
    ev_payload = [{
        "event_id": e.id, "date": e.event_date.isoformat() if e.event_date else None, "type": e.event_type, "category": e.category,
        "title": e.title, "summary": e.description, "verification": e.verification, "claim_type": e.claim_type,
        "materiality": e.materiality, "sentiment": e.sentiment, "is_new": e.id in new_ids, "source_refs": ev_refs[e.id],
    } for e in events]
    src_payload = [{"ref": s["ref"], "publisher": s["publisher"], "tier": s["tier_label"], "title": s["title"],
                    "published_at": s["published_at"], "excerpt": ((by_n[s["ref"]].snippet or "") + " " + (by_n[s["ref"]].body_text or ""))[:1200]}
                   for s in sources]
    user = json.dumps({
        "company": {"name": company.legal_name, "country": company.country_code, "industry": company.industry_text},
        "mode": mode, "window_days": window_days, "today": date.today().isoformat(),
        "previous_briefing_headline": prev_headline, "events": ev_payload, "sources": src_payload,
        "management_guidance": guidance[:10],
    }, ensure_ascii=False, default=str)
    try:
        out = llm.json(SYNTH_SYSTEM, user, SYNTH_SCHEMA, max_tokens=8000)
    except (LLMError, ValueError) as e:
        log.warning("LLM synthesis failed; falling back to rules: %s", e)
        return None
    valid_refs = set(by_n)
    valid_events = {e.id for e in events}
    ev_by_id = {e.id: e for e in events}
    dropped = []
    corpus_all = " ".join(f"{d.title} {d.snippet or ''} {d.body_text or ''}" for d in by_n.values()) + " " + json.dumps(guidance, default=str)

    def clean(items):
        good = []
        for it in items or []:
            refs = [r for r in it.get("source_refs", []) if r in valid_refs]
            it["event_ids"] = [x for x in it.get("event_ids", []) if x in valid_events]
            it["source_refs"] = refs
            if it["claim_type"] != "ai_interpretation" and not refs:
                dropped.append({"reason": "no valid citation", "text": it["text"]})
                continue
            corpus = " ".join(f"{by_n[r].title} {by_n[r].snippet or ''} {by_n[r].body_text or ''}" for r in refs) if refs else corpus_all
            if not _numbers_supported(it["text"], corpus + " " + " ".join(fmt_date(ev_by_id[x].event_date) + " " + str(ev_by_id[x].event_date) for x in it["event_ids"])):
                dropped.append({"reason": "number not found in cited sources", "text": it["text"]})
                continue
            first = ev_by_id.get(it["event_ids"][0]) if it["event_ids"] else None
            it.update({"date": first.event_date.isoformat() if first and first.event_date else None,
                       "materiality": first.materiality if first else "medium", "sentiment": first.sentiment if first else None,
                       "is_new": bool(first and first.id in new_ids), "verification": first.verification if first else None})
            good.append(it)
        return good

    secs = {k: clean(out.get(k)) for k in SECTION_TITLES}
    return {"headline": out.get("headline", ""), "summary": clean(out.get("summary")), "sections": secs, "dropped": dropped, "model": llm.name}


# ---------------------------------------------------------------- voice
def voice_script(company_name: str, report: dict) -> str:
    items = report["summary"][:5]
    today = datetime.now(timezone.utc).strftime("%d %B")
    if report.get("coverage_insufficient"):
        return (f"Here is your {company_name} briefing for {today}. I could not reach enough reliable public sources to assess "
                f"recent developments, so I am not drawing any conclusions. This is not evidence that nothing changed.")
    if report.get("no_material_change") or not items:
        return (f"Here is your {company_name} briefing for {today}. No material new developments were identified in the "
                f"latest public sources over the past {report['window_days']} days. This is an informational summary, not investment advice.")
    ordinals = ["First", "Second", "Third", "Fourth", "Fifth"]
    n = len(items)
    words = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five"}
    parts = [f"Here {'are' if n > 1 else 'is'} the {words[n]} development{'s' if n > 1 else ''} worth knowing about {company_name} for {today}."]
    company_only = all(it["claim_type"] == "company_claim" for it in items)
    if company_only:
        parts.append("All of them come from the company's own disclosures, not independent verification.")
    for i, it in enumerate(items):
        t = it["text"]
        if company_only:
            t = re.sub(r"^(The company stated: |In a disclosure to the stock exchange dated .+?, the company announced: )", "", t)
        t = re.sub(r"\s*\((?:[^()]|\([^)]*\))*\)", "", t).replace("“", "").replace("”", "").strip().rstrip(".")
        d = it.get("date")
        when = f"on {datetime.fromisoformat(d).strftime('%d %B %Y')}: " if d else ""
        parts.append(f"{ordinals[i]}, {when}{t}.")
    w = report["sections"].get("watch_next") or []
    if w:
        parts.append("What to watch next: " + w[0]["text"].replace("“", "").replace("”", "").removeprefix("Watch for ").rstrip(".") + ".")
    parts.append("Sources are listed in the written report. This is an informational summary, not investment advice.")
    return " ".join(parts)


# ---------------------------------------------------------------- orchestrator entry
def build_report(db: Session, company: m.Company, mode: str, window_days: int, coverage: list[dict], stats: dict) -> m.CompanyDailyReport:
    mode = mode if mode in MODES else "quick"
    weights, n_points = MODES[mode]
    since = date.today() - timedelta(days=window_days)
    min_mat = ("high", "medium", "low") if mode == "research" else ("high", "medium")
    events = db.execute(select(m.Event).where(m.Event.company_id == company.id, m.Event.event_date >= since)
                        .order_by(m.Event.materiality_score.desc(), m.Event.event_date.desc())).scalars().all()
    events_all = events
    events = [e for e in events if e.materiality in min_mat][:40] or events[:10]

    prev = db.execute(select(m.CompanyDailyReport).where(m.CompanyDailyReport.company_id == company.id)
                      .order_by(m.CompanyDailyReport.generated_at.desc())).scalars().first()
    prev_ids = set(prev.event_ids or []) if prev else set()
    new_ids = {e.id for e in events} - prev_ids

    sources, ev_refs, by_n = _sources_for_events(db, events)
    srcs_by_ref = {s["ref"]: s for s in sources}
    guidance = _guidance_items(db, company)

    # Cost control: nothing new since an LLM-written briefing with the same mode/window -> reuse its prose.
    reusable = (prev is not None and not new_ids and prev.mode == mode and prev.window_days == window_days
                and prev.synthesizer != "rules" and set(prev.event_ids or []) == {e.id for e in events})
    if reusable:
        pr = prev.report
        llm_out = {"sections": pr["sections"], "summary": pr["summary"], "headline": pr["headline"],
                   "dropped": [], "model": f"{prev.synthesizer} (reused, no new events)"}
    else:
        llm_out = llm_sections(company, events, ev_refs, sources, by_n, new_ids, guidance, mode, window_days,
                               (prev.report or {}).get("headline") if prev else None) if events else None
    if llm_out:
        sections, summary, headline, synth = llm_out["sections"], llm_out["summary"][:n_points], llm_out["headline"], llm_out["model"]
        stats["dropped_claims"] = llm_out["dropped"]
        if not sections.get("management_execution"):
            sections["management_execution"] = _rules_sections(events, ev_refs, srcs_by_ref, new_ids, company, guidance, weights)[0]["management_execution"]
    else:
        sections, summary = _rules_sections(events, ev_refs, srcs_by_ref, new_ids, company, guidance, weights)
        summary = summary[:n_points]
        synth = "rules"
        headline = None

    material = [e for e in events if e.materiality in ("high", "medium")]
    no_change = not material or (prev is not None and not (new_ids & {e.id for e in material}) and window_days <= 1)
    content_ok = [c for c in coverage if c["status"] == "ok"]
    insufficient = not content_ok
    if insufficient and not material:
        headline = ("Research coverage was insufficient to assess recent developments: none of the configured public sources "
                    "could be reached or applied to this company. This is not evidence that nothing changed.")
        no_change = False
    if not headline:
        if not material:
            headline = "No material new developments were identified in the latest public sources."
        else:
            top = sorted(material, key=lambda e: e.materiality_score, reverse=True)[0]
            headline = f"{len(material)} material development{'s' if len(material) != 1 else ''} in the last {window_days} day{'s' if window_days != 1 else ''}; most significant: {top.title.rstrip('.')}."

    freshest = max((d.published_at for d in by_n.values() if d.published_at), default=None)
    secs = company.securities
    exch = {e[0]: e[1] for e in EXCHANGES}
    gaps = [c for c in coverage if c["status"] != "ok"]
    report = {
        "company": {"id": company.id, "legal_name": company.legal_name, "country": company.country_code,
                    "industry": company.industry_text, "industry_provenance": (company.profile or {}).get("provenance", {}).get("industry"),
                    "lei": company.lei, "website": company.website, "description": (company.profile or {}).get("description"),
                    "listings": [{"exchange": exch.get(s.exchange_mic, s.exchange_mic), "ticker": s.ticker, "isin": s.isin} for s in secs]},
        "market": {"country": company.country_code, "exchanges": sorted({exch.get(s.exchange_mic, s.exchange_mic) for s in secs})},
        "mode": mode, "window_days": window_days,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data_freshness": freshest.isoformat() if freshest else None,
        "headline": headline, "no_material_change": no_change, "coverage_insufficient": insufficient,
        "summary": summary, "sections": sections, "section_titles": SECTION_TITLES,
        "management_guidance": guidance,
        "change_vs_previous": {"previous_report_at": prev.generated_at.isoformat() if prev else None,
                               "new_events": len(new_ids), "events_in_window": len(events_all)},
        "coverage": {"connectors": coverage, "gaps": [f"{g['connector']}: {g['note']}" for g in gaps if g.get("note")]},
        "sources": sources, "synthesizer": synth, "disclaimer": DISCLAIMER,
        "section_notes": {
            "progress": "Includes only primary-source facts, independently corroborated reports, or formal exchange disclosures of completed actions. Management claims alone are excluded.",
            "watch_next": "Monitoring suggestions generated from the evidence above (interpretation, not fact).",
        },
    }
    rep = m.CompanyDailyReport(company_id=company.id, mode=mode, window_days=window_days, report=report, synthesizer=synth,
                               data_freshness=freshest, event_ids=[e.id for e in events], stats=stats)
    rep.voice_script = voice_script(company.short_name or company.legal_name, report)
    report["voice_script"] = rep.voice_script
    db.add(rep)
    db.flush()
    report["report_id"] = rep.id
    rep.report = dict(report)
    for sec_name, items in [("summary", summary), *sections.items()]:
        for i, it in enumerate(items):
            for r in it.get("source_refs", []):
                db.add(m.Citation(report_id=rep.id, section=sec_name, item_index=i, document_id=by_n[r].id,
                                  event_id=(it.get("event_ids") or [None])[0]))
    db.flush()
    return rep
