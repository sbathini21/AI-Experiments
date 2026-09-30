import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.routes import router
from .config import get_settings
from sqlalchemy import update

from .db import SessionLocal, init_db
from .models import Job
from .pipeline.ingest import ensure_sources
from .tools.seed import seed_reference

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
settings = get_settings()

if settings.app_env == "prod" and settings.jwt_secret == "change-me-in-production":
    raise RuntimeError("Set JWT_SECRET in production")

@asynccontextmanager
async def lifespan(_app):
    init_db()
    with SessionLocal() as db:
        seed_reference(db)
        ensure_sources(db)
        if settings.run_jobs_inline:
            # Inline jobs die with the process: fail them so the next request starts a fresh one.
            db.execute(update(Job).where(Job.status.in_(["queued", "running"]))
                       .values(status="failed", error="interrupted by API restart"))
        db.commit()
    yield


app = FastAPI(title="Company Intelligence API", version="0.1.0", lifespan=lifespan,
              docs_url="/api/docs", openapi_url="/api/openapi.json")
app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
                   allow_credentials=True, allow_methods=["GET", "POST", "DELETE"], allow_headers=["*"])


@app.middleware("http")
async def security_headers(request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    return resp


app.include_router(router)
