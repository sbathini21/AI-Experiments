"""Pipeline orchestrator + job queue.

Single-flight: an analysis job has dedupe_key = company|mode|window|time-bucket, so concurrent requests for
the same company share one job, and a fresh report (< REPORT_TTL) is returned without any research at all.
"""
import json
import logging
import threading
import traceback
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import models as m
from ..config import get_settings
from ..db import SessionLocal, session_scope
from ..llm import LLMError, get_llm
from .extract import extract_events
from .ingest import collect_and_store
from .resolve import enrich_profile
from .synthesize import build_report

log = logging.getLogger(__name__)

GUIDANCE_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["assessments"],
    "properties": {"assessments": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["guidance_id", "status", "rationale", "event_id"],
        "properties": {"guidance_id": {"type": "string"},
                       "status": {"type": "string", "enum": ["on_track", "completed", "progressing", "delayed", "missed", "revised", "no_recent_evidence"]},
                       "rationale": {"type": "string"}, "event_id": {"type": "string"}}}}},
}


def track_guidance(db: Session, company: m.Company) -> int:
    """Update guidance status ONLY when a later stored event supports it (LLM-judged, event id must be valid)."""
    open_g = db.execute(select(m.ManagementGuidance).where(
        m.ManagementGuidance.company_id == company.id,
        m.ManagementGuidance.status.notin_(["completed", "missed"]))).scalars().all()
    llm = get_llm("cheap")
    if not open_g or not llm:
        return 0
    events = db.execute(select(m.Event).where(m.Event.company_id == company.id).order_by(m.Event.event_date.desc()).limit(60)).scalars().all()
    ev_ids = {e.id: e for e in events}
    payload = {
        "guidance": [{"guidance_id": g.id, "metric": g.metric, "target": g.target_value, "deadline": g.deadline_text,
                      "given_on": str(g.given_on), "statement": (db.get(m.ManagementStatement, g.statement_id).statement if g.statement_id else None),
                      "current_status": g.status} for g in open_g],
        "later_events": [{"event_id": e.id, "date": str(e.event_date), "type": e.event_type, "title": e.title,
                          "verification": e.verification} for e in events],
        "today": str(datetime.now(timezone.utc).date()),
    }
    system = ("Assess management guidance against later events. Change a status only if a listed event dated after given_on "
              "directly supports it; otherwise return the current status (or no_recent_evidence) and event_id ''. "
              "'missed' requires evidence the deadline passed without the target being met, not just an absence of news.")
    try:
        res = llm.json(system, json.dumps(payload, default=str), GUIDANCE_SCHEMA, max_tokens=3000)
    except (LLMError, ValueError) as e:
        log.warning("guidance tracking skipped: %s", e)
        return 0
    n = 0
    by_id = {g.id: g for g in open_g}
    for a in res.get("assessments", []):
        g = by_id.get(a["guidance_id"])
        ev = ev_ids.get(a.get("event_id"))
        if not g or a["status"] == g.status:
            continue
        if a["status"] != "no_recent_evidence" and (not ev or (g.given_on and ev.event_date and ev.event_date <= g.given_on)):
            continue  # no valid supporting event -> keep status
        db.add(m.GuidanceTracking(guidance_id=g.id, event_id=ev.id if ev else None, previous_status=g.status,
                                  new_status=a["status"], rationale=a["rationale"]))
        g.status = a["status"]
        n += 1
    return n


def analyze_company(company_id: str, mode: str = "quick", window_days: int = 7, progress=None) -> str:
    s = get_settings()
    progress = progress or (lambda msg: None)
    with session_scope() as db:
        c = db.get(m.Company, company_id)
        progress("enriching company profile (SEC / GLEIF / Wikidata)")
        enrich_profile(db, c)
        db.commit()  # commit per phase: keeps write transactions short (matters for SQLite)
        since = datetime.now(timezone.utc) - timedelta(days=max(window_days, s.lookback_days if window_days > 30 else 30))
        progress("collecting sources")
        ingest_stats = collect_and_store(db, c, since, progress)
        db.commit()
        ex_stats = extract_events(db, c, window_days, progress)
        db.commit()
        progress("tracking management guidance")
        g_updates = track_guidance(db, c)
        progress("synthesising report")
        stats = {**ingest_stats, **ex_stats, "guidance_status_updates": g_updates}
        rep = build_report(db, c, mode, window_days, ingest_stats["coverage"], stats)
        return rep.id


# ---------------------------------------------------------------- job queue
def _bucket(minutes: int) -> str:
    now = datetime.now(timezone.utc)
    return now.strftime("%Y%m%d%H") + str(now.minute // max(1, minutes))


def fresh_report(db: Session, company_id: str, mode: str, window_days: int) -> m.CompanyDailyReport | None:
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=get_settings().report_ttl_minutes)
    rep = db.execute(select(m.CompanyDailyReport).where(
        m.CompanyDailyReport.company_id == company_id, m.CompanyDailyReport.mode == mode,
        m.CompanyDailyReport.window_days == window_days).order_by(m.CompanyDailyReport.generated_at.desc())).scalars().first()
    if rep and rep.generated_at.replace(tzinfo=timezone.utc) >= cutoff:
        return rep
    return None


def enqueue_analysis(db: Session, company_id: str, mode: str, window_days: int, force: bool = False) -> m.Job:
    key = f"analyze|{company_id}|{mode}|{window_days}|{_bucket(get_settings().report_ttl_minutes)}"
    if force:
        key += f"|{datetime.now(timezone.utc).timestamp()}"
    existing = db.execute(select(m.Job).where(m.Job.dedupe_key == key)).scalar_one_or_none()
    if existing:
        report_ok = existing.status == "done" and existing.result and db.get(m.CompanyDailyReport, existing.result.get("report_id") or "")
        if existing.status in ("queued", "running") or report_ok:
            return existing
        existing.dedupe_key = f"{key}|retired|{existing.id}"  # failed / orphaned: free the key for a fresh job
        db.flush()
    job = m.Job(kind="analyze_company", dedupe_key=key, payload={"company_id": company_id, "mode": mode, "window_days": window_days}, progress=[])
    db.add(job)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return db.execute(select(m.Job).where(m.Job.dedupe_key == key)).scalar_one()
    if get_settings().run_jobs_inline:
        threading.Thread(target=run_job, args=(job.id,), daemon=True).start()
    return job


LIVE_PROGRESS: dict[str, list[str]] = {}  # in-process progress (inline mode)


def run_job(job_id: str) -> None:
    db = SessionLocal()
    try:
        # Claim atomically (works on Postgres and SQLite): only one runner flips queued->running.
        claimed = db.execute(update(m.Job).where(m.Job.id == job_id, m.Job.status == "queued")
                             .values(status="running", started_at=datetime.now(timezone.utc), attempts=m.Job.attempts + 1))
        db.commit()
        if claimed.rowcount != 1:
            return
        job = db.get(m.Job, job_id)
        log_lines: list[str] = []

        def progress(msg: str):
            log_lines.append(f"{datetime.now(timezone.utc).strftime('%H:%M:%S')} {msg}")
            LIVE_PROGRESS[job_id] = list(log_lines)
            if not get_settings().is_sqlite:  # separate worker process: persist progress for the API to read
                with session_scope() as s2:
                    s2.execute(update(m.Job).where(m.Job.id == job_id).values(progress=list(log_lines)))

        p = job.payload
        if job.kind == "analyze_company":
            rid = analyze_company(p["company_id"], p.get("mode", "quick"), p.get("window_days", 7), progress)
            result = {"report_id": rid}
        elif job.kind == "daily_refresh":
            result = {"reports": [analyze_company(cid, "quick", 1, progress) for cid in p["company_ids"]]}
        else:
            raise ValueError(f"unknown job kind {job.kind}")
        db.execute(update(m.Job).where(m.Job.id == job_id).values(status="done", result=result, progress=LIVE_PROGRESS.get(job_id, []), finished_at=datetime.now(timezone.utc)))
        db.commit()
    except Exception as e:
        db.rollback()
        log.error("job %s failed: %s", job_id, traceback.format_exc())
        db.execute(update(m.Job).where(m.Job.id == job_id).values(status="failed", error=str(e)[:1000], finished_at=datetime.now(timezone.utc)))
        db.commit()
    finally:
        db.close()
