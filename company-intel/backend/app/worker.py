"""Background worker: claims queued jobs and runs the daily watchlist refresh.

    python -m app.worker

Postgres-backed queue (SELECT ... FOR UPDATE SKIP LOCKED) — no Redis needed at MVP scale.
"""
import logging
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text

from . import models as m
from .config import get_settings
from .db import SessionLocal, init_db
from .pipeline.run import run_job

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s worker: %(message)s")
log = logging.getLogger("worker")


def next_job_id() -> str | None:
    with SessionLocal() as db:
        q = select(m.Job.id).where(m.Job.status == "queued").order_by(m.Job.created_at).limit(1)
        if not get_settings().is_sqlite:
            q = q.with_for_update(skip_locked=True)
        return db.execute(q).scalar_one_or_none()


def schedule_daily_refresh() -> None:
    """Once per day: one job refreshing every company on any watchlist (shared across users)."""
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    with SessionLocal() as db:
        key = f"daily_refresh|{today}"
        if db.execute(select(m.Job).where(m.Job.dedupe_key == key)).first():
            return
        ids = list(db.execute(select(m.WatchlistItem.company_id).distinct()).scalars())
        db.add(m.Job(kind="daily_refresh", dedupe_key=key, payload={"company_ids": ids}, progress=[]))
        db.commit()
        log.info("scheduled daily refresh for %d companies", len(ids))


def recover_stale() -> None:
    """Jobs left 'running' by a crashed worker are re-queued after 30 minutes."""
    with SessionLocal() as db:
        db.execute(text("UPDATE jobs SET status='queued' WHERE status='running' AND attempts < 3 AND started_at < :t"),
                   {"t": datetime.now(timezone.utc) - timedelta(minutes=30)})
        db.commit()


def main():
    init_db()
    s = get_settings()
    log.info("worker started (db=%s)", "sqlite" if s.is_sqlite else "postgres")
    last_maint = 0.0
    while True:
        if time.time() - last_maint > 300:
            recover_stale()
            if datetime.now(timezone.utc).hour == s.daily_refresh_hour_utc:
                schedule_daily_refresh()
            last_maint = time.time()
        jid = next_job_id()
        if jid:
            log.info("running job %s", jid)
            run_job(jid)
        else:
            time.sleep(2)


if __name__ == "__main__":
    main()
