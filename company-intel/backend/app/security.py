"""Auth (email+password, JWT in httpOnly cookie or Bearer), rate limiting and quotas."""
import hashlib
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import models as m
from .config import get_settings
from .db import get_db

COOKIE = "ci_session"


def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode()[:72], bcrypt.gensalt(rounds=12)).decode()


def verify_password(pw: str, h: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode()[:72], h.encode())
    except ValueError:
        return False


def make_token(user_id: str) -> str:
    s = get_settings()
    exp = datetime.now(timezone.utc) + timedelta(hours=s.jwt_ttl_hours)
    return jwt.encode({"sub": user_id, "exp": exp}, s.jwt_secret, algorithm="HS256")


def _token_from(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:]
    return request.cookies.get(COOKIE)


def current_user_optional(request: Request, db: Session = Depends(get_db)) -> m.User | None:
    tok = _token_from(request)
    if not tok:
        return None
    try:
        data = jwt.decode(tok, get_settings().jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    return db.get(m.User, data["sub"])


def current_user(user: m.User | None = Depends(current_user_optional)) -> m.User:
    if not user:
        raise HTTPException(401, "Sign in required")
    return user


def client_key(request: Request) -> str:
    ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "unknown").split(",")[0].strip()
    return hashlib.sha256(f"{ip}|{get_settings().jwt_secret}".encode()).hexdigest()[:32]  # never store raw IPs


class RateLimiter:
    """Sliding-window limiter, in-process (single API instance on the MVP VPS). Swap for Redis when scaling out."""

    def __init__(self):
        self.hits: dict[str, deque] = defaultdict(deque)
        self.lock = threading.Lock()

    def check(self, key: str, limit: int, window_s: int = 60) -> bool:
        now = time.monotonic()
        with self.lock:
            q = self.hits[key]
            while q and q[0] < now - window_s:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            return True


limiter = RateLimiter()


def rate_limit(request: Request):
    # Cheap status polling gets its own, higher bucket so it can't starve real requests (or vice versa).
    polling = request.url.path.startswith("/api/jobs/")
    key = client_key(request) + ("|poll" if polling else "")
    limit = 120 if polling else get_settings().rate_limit_per_minute
    if not limiter.check(key, limit):
        raise HTTPException(429, "Too many requests — please slow down.")


def enforce_analysis_quota(db: Session, user: m.User | None, ckey: str) -> None:
    s = get_settings()
    since = datetime.now(timezone.utc) - timedelta(days=1)
    q = select(func.count(m.ResearchQuery.id)).where(m.ResearchQuery.created_at >= since, m.ResearchQuery.triggered_research.is_(True))
    if user:
        n, cap = db.scalar(q.where(m.ResearchQuery.user_id == user.id)), s.user_analyses_per_day
    else:
        n, cap = db.scalar(q.where(m.ResearchQuery.client_key == ckey)), s.anon_analyses_per_day
    if n >= cap:
        raise HTTPException(429, f"Daily research limit reached ({cap}). {'Sign in for a higher limit.' if not user else ''}".strip())
