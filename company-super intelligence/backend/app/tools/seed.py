"""Reference data + securities master import.

    python -m app.tools.seed --sec          # US: SEC company_tickers_exchange.json (~10k issuers)
    python -m app.tools.seed --nse          # India: NSE EQUITY_L.csv (public archive download)
    python -m app.tools.seed --bse-csv FILE # India: BSE "List of Scrips" CSV downloaded manually from bseindia.com
    python -m app.tools.seed --lse-csv FILE # UK: CSV with columns ticker,name,isin (e.g. from LSE instrument list)

Companies missing from the master are still found at query time through OpenFIGI.
"""
import argparse
import csv
import io
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models as m
from ..db import SessionLocal, init_db
from ..reference import COUNTRIES, EXCHANGES
from ..sources.http import fetch

log = logging.getLogger(__name__)

SEC_EXCH = {"NYSE": "XNYS", "Nasdaq": "XNAS", "NYSE American": "XASE", "NYSE Arca": "ARCX", "OTC": "OTCM", "CBOE": "XNYS"}


def seed_reference(db: Session) -> None:
    for code, name, cur, tz, reg in COUNTRIES:
        if not db.get(m.Country, code):
            db.add(m.Country(code=code, name=name, currency=cur, timezone=tz, regulator=reg))
    db.flush()
    for mic, code, name, cc, figi in EXCHANGES:
        if not db.get(m.Exchange, mic):
            db.add(m.Exchange(mic=mic, code=code, name=name, country_code=cc, figi_exch_code=figi))
    db.flush()


def _upsert(db: Session, *, name: str, country: str, mic: str, ticker: str, isin: str | None = None, ids: dict | None = None,
            index: dict | None = None) -> None:
    sec = index.get((mic, ticker)) if index is not None else db.execute(
        select(m.Security).where(m.Security.exchange_mic == mic, m.Security.ticker == ticker)).scalar_one_or_none()
    if sec:
        if isin and not sec.isin:
            sec.isin = isin
        return
    company = None
    if isin:
        other = db.execute(select(m.Security).where(m.Security.isin == isin)).scalars().first()
        company = other.company if other else None
    if not company:
        company = m.Company(legal_name=name, country_code=country, identifiers=ids or {})
        db.add(company)
    s = m.Security(company=company, exchange_mic=mic, ticker=ticker, isin=isin, is_primary=not company.securities)
    db.add(s)
    if index is not None:
        index[(mic, ticker)] = s


def import_sec(db: Session) -> int:
    r = fetch("https://www.sec.gov/files/company_tickers_exchange.json", ttl=0, timeout=60)
    r_data = r.json()
    fields = r_data["fields"]
    index = {(s.exchange_mic, s.ticker): s for s in db.execute(select(m.Security)).scalars()}
    by_cik: dict[int, m.Company] = {}
    n = 0
    for row in r_data["data"]:
        rec = dict(zip(fields, row))
        mic = SEC_EXCH.get(rec.get("exchange") or "")
        if not mic or not rec.get("ticker"):
            continue
        ticker = rec["ticker"].upper()
        if (mic, ticker) in index:
            continue
        cik = int(rec["cik"])
        company = by_cik.get(cik)
        if not company:
            company = m.Company(legal_name=rec["name"], country_code="US", identifiers={"sec_cik": str(cik).zfill(10)})
            db.add(company)
            by_cik[cik] = company
        s = m.Security(company=company, exchange_mic=mic, ticker=ticker, is_primary=not company.securities)
        db.add(s)
        index[(mic, ticker)] = s
        n += 1
    db.commit()
    return n


def import_nse(db: Session) -> int:
    r = fetch("https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv", ttl=0, timeout=60,
              headers={"User-Agent": "Mozilla/5.0 (compatible; CompanyIntel/0.1)"})
    rows = list(csv.DictReader(io.StringIO(r.text)))
    index = {(s.exchange_mic, s.ticker): s for s in db.execute(select(m.Security)).scalars()}
    for row in rows:
        row = {k.strip(): (v or "").strip() for k, v in row.items()}
        _upsert(db, name=row["NAME OF COMPANY"], country="IN", mic="XNSE", ticker=row["SYMBOL"], isin=row.get("ISIN NUMBER"), index=index)
    db.commit()
    return len(rows)


def import_csv(db: Session, path: str, mic: str, country: str, cols: dict) -> int:
    index = {(s.exchange_mic, s.ticker): s for s in db.execute(select(m.Security)).scalars()}
    n = 0
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            row = {k.strip(): (v or "").strip() for k, v in row.items() if k}
            t, name = row.get(cols["ticker"]), row.get(cols["name"])
            if not t or not name:
                continue
            ids = {"bse_code": row.get("Security Code")} if mic == "XBOM" and row.get("Security Code") else None
            _upsert(db, name=name, country=country, mic=mic, ticker=t, isin=row.get(cols.get("isin", "")) or None, ids=ids, index=index)
            n += 1
    db.commit()
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sec", action="store_true")
    ap.add_argument("--nse", action="store_true")
    ap.add_argument("--bse-csv")
    ap.add_argument("--lse-csv")
    a = ap.parse_args()
    init_db()
    with SessionLocal() as db:
        seed_reference(db)
        db.commit()
        if a.sec:
            print("SEC securities added:", import_sec(db))
        if a.nse:
            print("NSE rows processed:", import_nse(db))
        if a.bse_csv:
            print("BSE rows processed:", import_csv(db, a.bse_csv, "XBOM", "IN", {"ticker": "Security Id", "name": "Security Name", "isin": "ISIN No"}))
        if a.lse_csv:
            print("LSE rows processed:", import_csv(db, a.lse_csv, "XLON", "GB", {"ticker": "ticker", "name": "name", "isin": "isin"}))


if __name__ == "__main__":
    main()
