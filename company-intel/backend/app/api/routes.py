import json
import re
from datetime import date, datetime, timedelta, timezone
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models as m
from ..config import get_settings
from ..db import get_db
from ..llm import LLMError, get_llm
from ..pipeline.extract import jaccard, tokens
from ..pipeline.resolve import resolve
from ..pipeline.run import LIVE_PROGRESS, enqueue_analysis, fresh_report
from ..reference import TIER_LABELS, domain_of
from ..security import (COOKIE, client_key, current_user, current_user_optional, enforce_analysis_quota, hash_password,
                        make_token, rate_limit, verify_password)

router = APIRouter(prefix="/api", dependencies=[Depends(rate_limit)])
Mode = Literal["quick", "trader", "long_term", "research", "institutional"]
WINDOWS = {"24h": 1, "7d": 7, "30d": 30, "90d": 90, "1y": 365, "3y": 1095}


# ---------------------------------------------------------------- auth
class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


def _set_cookie(resp: Response, token: str):
    resp.set_cookie(COOKIE, token, httponly=True, samesite="lax", secure=get_settings().app_env == "prod",
                    max_age=get_settings().jwt_ttl_hours * 3600)


@router.post("/auth/signup")
def signup(body: Credentials, response: Response, db: Session = Depends(get_db)):
    if db.execute(select(m.User).where(m.User.email == body.email.lower())).first():
        raise HTTPException(409, "An account with this email already exists")
    u = m.User(email=body.email.lower(), password_hash=hash_password(body.password))
    db.add(u)
    db.commit()
    _set_cookie(response, make_token(u.id))
    return {"id": u.id, "email": u.email}


@router.post("/auth/login")
def login(body: Credentials, response: Response, db: Session = Depends(get_db)):
    u = db.execute(select(m.User).where(m.User.email == body.email.lower())).scalar_one_or_none()
    if not u or not u.password_hash or not verify_password(body.password, u.password_hash):
        raise HTTPException(401, "Invalid email or password")
    u.last_login_at = datetime.now(timezone.utc)
    db.commit()
    _set_cookie(response, make_token(u.id))
    return {"id": u.id, "email": u.email}


@router.post("/auth/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE)
    return {"ok": True}


@router.get("/auth/me")
def me(user: m.User | None = Depends(current_user_optional)):
    return {"user": {"id": user.id, "email": user.email, "preferred_mode": user.preferred_mode} if user else None}


# ---------------------------------------------------------------- company resolution & analysis
class ResolveIn(BaseModel):
    query: str = Field(min_length=1, max_length=300)


@router.post("/company/resolve")
def company_resolve(body: ResolveIn, db: Session = Depends(get_db)):
    return resolve(db, body.query)


class AnalyzeIn(BaseModel):
    company_id: str = Field(max_length=36)
    mode: Mode = "quick"
    window_days: int = Field(7, ge=1, le=1095)
    force: bool = False
    raw_query: str | None = Field(None, max_length=300)
    input_mode: Literal["text", "voice"] = "text"


def _start_analysis(db: Session, request: Request, user, company_id, mode, window_days, force, raw_query, input_mode):
    if not db.get(m.Company, company_id):
        raise HTTPException(404, "Company not found")
    ckey = client_key(request)
    rq = m.ResearchQuery(user_id=user.id if user else None, client_key=ckey, raw_query=raw_query or company_id,
                         input_mode=input_mode, company_id=company_id, parsed={"mode": mode, "window_days": window_days})
    rep = None if force else fresh_report(db, company_id, mode, window_days)
    if rep:
        rq.report_id = rep.id
        db.add(rq)
        db.commit()
        return {"status": "ready", "cached": True, "report": rep.report}
    enforce_analysis_quota(db, user, ckey)
    job = enqueue_analysis(db, company_id, mode, window_days, force)
    rq.job_id, rq.triggered_research = job.id, True
    db.add(rq)
    db.commit()
    return {"status": job.status, "job_id": job.id}


@router.post("/company/analyze")
def company_analyze(body: AnalyzeIn, request: Request, db: Session = Depends(get_db), user=Depends(current_user_optional)):
    return _start_analysis(db, request, user, body.company_id, body.mode, body.window_days, body.force, body.raw_query, body.input_mode)


class SearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=300)
    mode: Mode = "quick"
    input_mode: Literal["text", "voice"] = "text"


@router.post("/search")
def search(body: SearchIn, request: Request, db: Session = Depends(get_db), user=Depends(current_user_optional)):
    """Natural-language entry point: 'What changed at Kinetic Engineering this week?' -> resolve -> analyze."""
    res = resolve(db, body.query)
    if res["status"] != "resolved":
        return {"resolution": res}
    out = _start_analysis(db, request, user, res["selected"]["company_id"], body.mode, res["query"]["window_days"],
                          False, body.query, body.input_mode)
    return {"resolution": res, **out}


@router.get("/jobs/{job_id}")
def job_status(job_id: str, db: Session = Depends(get_db)):
    j = db.get(m.Job, job_id)
    if not j:
        raise HTTPException(404, "Job not found")
    out = {"id": j.id, "status": j.status, "progress": LIVE_PROGRESS.get(j.id) or j.progress or [], "error": j.error}
    if j.status == "done" and j.result and j.result.get("report_id"):
        rep = db.get(m.CompanyDailyReport, j.result["report_id"])
        out["report"] = rep.report if rep else None
    return out


def _company_or_404(db, cid) -> m.Company:
    c = db.get(m.Company, cid)
    if not c:
        raise HTTPException(404, "Company not found")
    return c


@router.get("/company/{cid}")
def company_get(cid: str, db: Session = Depends(get_db)):
    c = _company_or_404(db, cid)
    return {"id": c.id, "legal_name": c.legal_name, "country": c.country_code, "industry": c.industry_text, "lei": c.lei,
            "website": c.website, "identifiers": c.identifiers, "profile": c.profile,
            "listings": [{"exchange_mic": s.exchange_mic, "ticker": s.ticker, "isin": s.isin, "figi": s.figi} for s in c.securities]}


@router.get("/company/{cid}/latest")
def company_latest(cid: str, mode: Mode | None = None, db: Session = Depends(get_db)):
    q = select(m.CompanyDailyReport).where(m.CompanyDailyReport.company_id == cid)
    if mode:
        q = q.where(m.CompanyDailyReport.mode == mode)
    rep = db.execute(q.order_by(m.CompanyDailyReport.generated_at.desc())).scalars().first()
    if not rep:
        raise HTTPException(404, "No report yet — POST /api/company/analyze first")
    return rep.report


def _event_out(db, e: m.Event) -> dict:
    docs = db.execute(select(m.Document).join(m.EventSource, m.EventSource.document_id == m.Document.id)
                      .where(m.EventSource.event_id == e.id).order_by(m.Document.credibility_tier)).scalars().all()
    return {"id": e.id, "date": e.event_date.isoformat() if e.event_date else None, "type": e.event_type, "category": e.category,
            "title": e.title, "description": e.description, "materiality": e.materiality, "verification": e.verification,
            "claim_type": e.claim_type, "sentiment": e.sentiment, "confidence": e.confidence,
            "sources": [{"title": d.title, "url": d.url, "publisher": (d.raw or {}).get("publisher") or domain_of(d.url),
                         "tier": d.credibility_tier, "tier_label": TIER_LABELS.get(d.credibility_tier)} for d in docs]}


@router.get("/company/{cid}/timeline")
def company_timeline(cid: str, window: str = "30d", db: Session = Depends(get_db)):
    days = WINDOWS.get(window)
    if not days:
        raise HTTPException(422, f"window must be one of {list(WINDOWS)}")
    _company_or_404(db, cid)
    since = date.today() - timedelta(days=days)
    evs = db.execute(select(m.Event).where(m.Event.company_id == cid, m.Event.event_date >= since)
                     .order_by(m.Event.event_date.desc())).scalars().all()
    return {"window": window, "events": [_event_out(db, e) for e in evs]}


@router.get("/company/{cid}/events")
def company_events(cid: str, event_type: str | None = None, materiality: str | None = None, limit: int = 100, db: Session = Depends(get_db)):
    q = select(m.Event).where(m.Event.company_id == cid)
    if event_type:
        q = q.where(m.Event.event_type == event_type)
    if materiality:
        q = q.where(m.Event.materiality == materiality)
    evs = db.execute(q.order_by(m.Event.event_date.desc()).limit(min(limit, 500))).scalars().all()
    return {"events": [_event_out(db, e) for e in evs]}


@router.get("/company/{cid}/guidance")
def company_guidance(cid: str, db: Session = Depends(get_db)):
    from ..pipeline.synthesize import _guidance_items
    return {"guidance": _guidance_items(db, _company_or_404(db, cid))}


@router.get("/company/{cid}/competitors")
def company_competitors(cid: str, db: Session = Depends(get_db)):
    c = _company_or_404(db, cid)
    comps = db.execute(select(m.Competitor).where(m.Competitor.company_id == cid)).scalars().all()
    peers = []
    if c.industry_id:
        peers = db.execute(select(m.Company).where(m.Company.industry_id == c.industry_id, m.Company.id != cid).limit(15)).scalars().all()
    return {
        "competitors": [{"name": x.competitor_name, "company_id": x.competitor_company_id, "basis": x.basis, "confidence": x.confidence} for x in comps],
        "same_industry_in_database": [{"company_id": p.id, "name": p.legal_name, "country": p.country_code} for p in peers],
        "note": "Same-industry companies are listed by classification only; this is not a competitive assessment.",
    }


class AskIn(BaseModel):
    question: str = Field(min_length=2, max_length=500)


ASK_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["answer", "event_ids", "could_not_verify"],
              "properties": {"answer": {"type": "string"}, "event_ids": {"type": "array", "items": {"type": "string"}},
                             "could_not_verify": {"type": "boolean"}}}


@router.post("/company/{cid}/ask")
def company_ask(cid: str, body: AskIn, db: Session = Depends(get_db)):
    """Follow-up questions answered from the stored event database (no new web research)."""
    c = _company_or_404(db, cid)
    evs = db.execute(select(m.Event).where(m.Event.company_id == cid).order_by(m.Event.event_date.desc()).limit(300)).scalars().all()
    qt = tokens(body.question)
    ranked = sorted(evs, key=lambda e: jaccard(qt, tokens(f"{e.title} {e.description or ''} {e.event_type.replace('_', ' ')}")) + e.materiality_score * 0.05, reverse=True)[:15]
    from ..pipeline.synthesize import _guidance_items
    guidance = _guidance_items(db, c)
    llm = get_llm("cheap")
    if not llm:
        return {"answer": None, "mode": "retrieval_only",
                "message": "Conversational answers need an LLM key; here are the most relevant stored events.",
                "events": [_event_out(db, e) for e in ranked[:6]]}
    payload = {"company": c.legal_name, "question": body.question, "today": str(date.today()),
               "events": [{"event_id": e.id, "date": str(e.event_date), "type": e.event_type, "title": e.title, "summary": e.description,
                           "verification": e.verification, "claim_type": e.claim_type} for e in ranked],
               "management_guidance": guidance[:8]}
    system = ("Answer the user's follow-up question about the company using ONLY the provided stored events and guidance. "
              "Cite by listing the event_ids you used. If the answer is not in the data, set could_not_verify=true and say "
              "'I could not verify this from a reliable public source in the stored data.' Keep it under 120 words, "
              "keep facts vs company claims vs unverified separate, and give no investment advice. If asked to explain simply, use plain language.")
    try:
        out = llm.json(system, json.dumps(payload, default=str), ASK_SCHEMA, max_tokens=1500)
    except (LLMError, ValueError) as e:
        raise HTTPException(503, f"Answer service unavailable: {e}")
    valid = {e.id: e for e in ranked}
    used = [valid[x] for x in out.get("event_ids", []) if x in valid]
    return {"answer": out["answer"], "could_not_verify": out["could_not_verify"] or not used, "events": [_event_out(db, e) for e in used]}


@router.get("/reports/{rid}")
def report_get(rid: str, db: Session = Depends(get_db)):
    rep = db.get(m.CompanyDailyReport, rid)
    if not rep:
        raise HTTPException(404, "Report not found")
    return rep.report


@router.get("/history")
def history(user=Depends(current_user), db: Session = Depends(get_db)):
    rows = db.execute(select(m.ResearchQuery, m.Company).join(m.Company, m.ResearchQuery.company_id == m.Company.id)
                      .where(m.ResearchQuery.user_id == user.id).order_by(m.ResearchQuery.created_at.desc()).limit(50)).all()
    return {"history": [{"query": q.raw_query, "company_id": c.id, "company": c.legal_name, "at": q.created_at.isoformat(),
                         "report_id": q.report_id, "job_id": q.job_id, "input_mode": q.input_mode} for q, c in rows]}


# ---------------------------------------------------------------- watchlist & alerts
def _watchlist(db, user) -> m.Watchlist:
    wl = db.execute(select(m.Watchlist).where(m.Watchlist.user_id == user.id)).scalars().first()
    if not wl:
        wl = m.Watchlist(user_id=user.id)
        db.add(wl)
        db.flush()
    return wl


class WatchIn(BaseModel):
    company_id: str = Field(max_length=36)


@router.get("/watchlist")
def watchlist_get(user=Depends(current_user), db: Session = Depends(get_db)):
    wl = _watchlist(db, user)
    db.commit()
    out = []
    for it in wl.items:
        rep = db.execute(select(m.CompanyDailyReport).where(m.CompanyDailyReport.company_id == it.company_id)
                         .order_by(m.CompanyDailyReport.generated_at.desc())).scalars().first()
        out.append({"company_id": it.company_id, "name": it.company.legal_name, "country": it.company.country_code,
                    "added_at": it.added_at.isoformat(),
                    "latest": {"headline": rep.report.get("headline"), "generated_at": rep.generated_at.isoformat(),
                               "new_events": rep.report.get("change_vs_previous", {}).get("new_events"), "report_id": rep.id} if rep else None})
    return {"watchlist": {"id": wl.id, "name": wl.name, "items": out}}


@router.post("/watchlist")
def watchlist_add(body: WatchIn, user=Depends(current_user), db: Session = Depends(get_db)):
    _company_or_404(db, body.company_id)
    wl = _watchlist(db, user)
    if not any(i.company_id == body.company_id for i in wl.items):
        if len(wl.items) >= 50:
            raise HTTPException(400, "Watchlist limit is 50 companies in the MVP")
        db.add(m.WatchlistItem(watchlist_id=wl.id, company_id=body.company_id))
    db.commit()
    return {"ok": True}


@router.delete("/watchlist/{cid}")
def watchlist_remove(cid: str, user=Depends(current_user), db: Session = Depends(get_db)):
    wl = _watchlist(db, user)
    for it in list(wl.items):
        if it.company_id == cid:
            db.delete(it)
    db.commit()
    return {"ok": True}


class AlertIn(BaseModel):
    company_id: str | None = Field(None, max_length=36)
    event_types: list[str] | None = None
    min_materiality: Literal["high", "medium", "low"] = "high"
    channel: Literal["web", "email"] = "web"


@router.post("/alerts")
def alerts_create(body: AlertIn, user=Depends(current_user), db: Session = Depends(get_db)):
    a = m.Alert(user_id=user.id, **body.model_dump())
    db.add(a)
    db.commit()
    return {"id": a.id}


@router.get("/alerts")
def alerts_list(user=Depends(current_user), db: Session = Depends(get_db)):
    rows = db.execute(select(m.Alert).where(m.Alert.user_id == user.id)).scalars().all()
    return {"alerts": [{"id": a.id, "company_id": a.company_id, "event_types": a.event_types, "min_materiality": a.min_materiality,
                        "channel": a.channel, "active": a.active} for a in rows]}


# ---------------------------------------------------------------- voice
@router.post("/voice/transcribe")
async def voice_transcribe(audio: UploadFile = File(...)):
    """Server-side STT (OpenAI). The web client uses the browser's free Web Speech API first and only falls back here."""
    key = get_settings().openai_api_key
    if not key:
        raise HTTPException(501, "Server transcription not configured; use browser speech recognition.")
    data = await audio.read()
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(413, "Audio too large (10 MB max)")
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post("https://api.openai.com/v1/audio/transcriptions", headers={"Authorization": f"Bearer {key}"},
                         files={"file": (audio.filename or "audio.webm", data, audio.content_type or "audio/webm")},
                         data={"model": "whisper-1"})
    if r.status_code >= 400:
        raise HTTPException(502, "Transcription failed")
    return {"text": r.json().get("text", "")}


class SynthIn(BaseModel):
    report_id: str = Field(max_length=36)
    voice: str = Field("alloy", max_length=20)


@router.post("/voice/synthesize")
async def voice_synthesize(body: SynthIn, db: Session = Depends(get_db)):
    """Server-side TTS (OpenAI) with caching in audio_reports. Browser speechSynthesis is the free default."""
    rep = db.get(m.CompanyDailyReport, body.report_id)
    if not rep:
        raise HTTPException(404, "Report not found")
    cached = db.execute(select(m.AudioReport).where(m.AudioReport.report_id == rep.id, m.AudioReport.voice == body.voice)).scalars().first()
    if cached:
        return FileResponse(cached.storage_path, media_type="audio/mpeg")
    key = get_settings().openai_api_key
    if not key:
        raise HTTPException(501, "Server TTS not configured; use browser speech synthesis.")
    async with httpx.AsyncClient(timeout=90) as c:
        r = await c.post("https://api.openai.com/v1/audio/speech", headers={"Authorization": f"Bearer {key}"},
                         json={"model": "tts-1", "voice": body.voice, "input": rep.voice_script[:4000]})
    if r.status_code >= 400:
        raise HTTPException(502, "Speech synthesis failed")
    import os
    os.makedirs("data/audio", exist_ok=True)
    path = f"data/audio/{rep.id}-{re.sub(r'[^a-z]', '', body.voice)}.mp3"
    with open(path, "wb") as f:
        f.write(r.content)
    db.add(m.AudioReport(report_id=rep.id, provider="openai:tts-1", voice=body.voice, storage_path=path,
                         duration_seconds=round(len(rep.voice_script.split()) / 2.5, 1)))
    db.commit()
    return FileResponse(path, media_type="audio/mpeg")


@router.get("/health")
def health():
    s = get_settings()
    return {"ok": True, "llm": s.llm_enabled, "llm_cheap": s.llm_cheap if s.llm_enabled else None,
            "llm_strong": s.llm_strong if s.llm_enabled else None, "time": datetime.now(timezone.utc).isoformat()}
