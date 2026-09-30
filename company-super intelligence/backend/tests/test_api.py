"""API smoke tests with the network stubbed out."""
import os

os.environ["DATABASE_URL"] = "sqlite:///./data/test_api.db"
os.environ["RUN_JOBS_INLINE"] = "false"
if os.path.exists("data/test_api.db"):
    os.remove("data/test_api.db")

from fastapi.testclient import TestClient  # noqa: E402

from app import models as m  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.pipeline import resolve as resolve_mod  # noqa: E402
from app.pipeline import run as run_mod  # noqa: E402


def setup_module():
    resolve_mod.search_openfigi = lambda q, limit=8: []  # no network
    with TestClient(app):
        pass
    with SessionLocal() as db:
        c = m.Company(legal_name="Kinetic Engineering Limited", country_code="IN")
        db.add(c)
        db.add(m.Security(company=c, exchange_mic="XBOM", ticker="KNEL", is_primary=True))
        for name, t in [("Tata Motors Limited", "TMCV"), ("Tata Motors Passenger Vehicles Limited", "TMPV")]:
            x = m.Company(legal_name=name, country_code="IN")
            db.add(x)
            db.add(m.Security(company=x, exchange_mic="XNSE", ticker=t))
        db.commit()


def test_resolve_unique_and_ambiguous():
    with TestClient(app) as c:
        r = c.post("/api/company/resolve", json={"query": "Give me today's update on Kinetic Engineering."}).json()
        assert r["status"] == "resolved" and r["selected"]["name"] == "Kinetic Engineering Limited"
        r = c.post("/api/company/resolve", json={"query": "Tata Motors"}).json()
        assert r["status"] == "ambiguous" and len(r["candidates"]) >= 2  # never silently pick


def test_analyze_enqueues_single_flight_job():
    with TestClient(app) as c:
        cid = c.post("/api/company/resolve", json={"query": "Kinetic Engineering"}).json()["selected"]["company_id"]
        a = c.post("/api/company/analyze", json={"company_id": cid}).json()
        b = c.post("/api/company/analyze", json={"company_id": cid}).json()
        assert a["job_id"] == b["job_id"]  # concurrent requests share one research job
        assert c.get(f"/api/jobs/{a['job_id']}").json()["status"] == "queued"


def test_auth_and_watchlist():
    with TestClient(app) as c:
        assert c.get("/api/watchlist").status_code == 401
        assert c.post("/api/auth/signup", json={"email": "t@example.com", "password": "correct horse"}).status_code == 200
        cid = c.post("/api/company/resolve", json={"query": "Kinetic Engineering"}).json()["selected"]["company_id"]
        assert c.post("/api/watchlist", json={"company_id": cid}).json()["ok"]
        items = c.get("/api/watchlist").json()["watchlist"]["items"]
        assert [i["name"] for i in items] == ["Kinetic Engineering Limited"]
        assert c.post("/api/auth/login", json={"email": "t@example.com", "password": "wrong password"}).status_code == 401


def test_input_validation():
    with TestClient(app) as c:
        assert c.post("/api/company/resolve", json={"query": ""}).status_code == 422
        assert c.post("/api/company/analyze", json={"company_id": "x", "mode": "yolo"}).status_code == 422
        assert c.get("/api/company/does-not-exist/timeline?window=7d").status_code == 404
