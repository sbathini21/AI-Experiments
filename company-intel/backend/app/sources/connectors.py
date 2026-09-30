"""Data connectors. Each one documents its licensing position; disabled connectors report why.

Implemented against official/permitted interfaces only:
  * SEC EDGAR submissions API (US, public domain, fair-access rate limit)
  * Companies House API (UK, OGL, free key)
  * GDELT DOC 2.0 API (global news discovery, open; 1 request / 5s)
  * Brave Search API (licensed commercial web search; finds IR pages, industry media, public LinkedIn snippets)
  * Google News RSS (personal/non-commercial only -> disabled by default)
  * Indian exchange announcements: NSE/BSE websites block automated access and their terms restrict it.
    Production needs a licensed feed; the connector is a placeholder that reports the gap honestly.
"""
import html
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from ..config import get_settings
from .base import CompanyContext, Connector, RawDoc
from .http import fetch

log = logging.getLogger(__name__)

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")


def html_to_text(s: str, limit: int = 20000) -> str:
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", s)
    s = TAG_RE.sub(" ", s)
    return WS_RE.sub(" ", html.unescape(s)).strip()[:limit]


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ------------------------------------------------------------------ SEC EDGAR
class SecEdgar(Connector):
    name = "sec_edgar"
    countries = ("US",)
    MAX_BODY_FETCH = 6

    def applies(self, ctx):
        return bool(ctx.identifiers.get("sec_cik"))

    def collect(self, ctx: CompanyContext, since: datetime) -> list[RawDoc]:
        from ..pipeline.taxonomy import SEC_8K_ITEMS, SEC_FORMS

        cik = str(ctx.identifiers["sec_cik"]).zfill(10)
        r = fetch(f"https://data.sec.gov/submissions/CIK{cik}.json", ttl=900)
        if r.status_code != 200:
            raise RuntimeError(f"SEC submissions HTTP {r.status_code}")
        recent = r.json().get("filings", {}).get("recent", {})
        docs, bodies = [], 0
        for i, form in enumerate(recent.get("form", [])):
            fdate = datetime.strptime(recent["filingDate"][i], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            if fdate < since:
                break  # list is newest-first
            if form not in SEC_FORMS and form not in ("8-K", "8-K/A"):
                continue
            acc = recent["accessionNumber"][i]
            primary = recent["primaryDocument"][i]
            url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace('-', '')}/{primary}"
            items = [x.strip() for x in (recent.get("items", [""] * (i + 1))[i] or "").split(",") if x.strip()]
            desc = recent.get("primaryDocDescription", [""] * (i + 1))[i] or ""
            if form.startswith("8-K"):
                hint = next((SEC_8K_ITEMS[x] for x in items if x in SEC_8K_ITEMS and SEC_8K_ITEMS[x] != "other"), "other")
                title = f"Form {form} filed" + (f" — items {', '.join(items)}" if items else "")
            else:
                hint, title = SEC_FORMS[form]
            body = None
            if form.startswith("8-K") and bodies < self.MAX_BODY_FETCH:
                br = fetch(url, ttl=86400)
                if br.status_code == 200:
                    body = html_to_text(br.text)
                    bodies += 1
            docs.append(RawDoc(
                connector=self.name, doc_type="filing", url=url, title=title, published_at=fdate,
                snippet=desc or None, body_text=body, regulator="SEC", form_type=form, filing_id=acc,
                items=items, event_type_hint=hint, publisher="SEC EDGAR",
            ))
        return docs


# ------------------------------------------------------------------ Companies House (UK)
class CompaniesHouse(Connector):
    name = "companies_house"
    countries = ("GB",)

    def enabled(self):
        if not get_settings().companies_house_api_key:
            return False, "COMPANIES_HOUSE_API_KEY not set (free key from developer.company-information.service.gov.uk)"
        return True, None

    def applies(self, ctx):
        return ctx.country_code == "GB" and bool(ctx.identifiers.get("companies_house"))

    def collect(self, ctx, since):
        import base64

        num = ctx.identifiers["companies_house"]
        token = base64.b64encode(f"{get_settings().companies_house_api_key}:".encode()).decode()
        r = fetch(f"https://api.company-information.service.gov.uk/company/{num}/filing-history",
                  params={"items_per_page": 50}, headers={"Authorization": f"Basic {token}"}, ttl=1800)
        if r.status_code != 200:
            raise RuntimeError(f"Companies House HTTP {r.status_code}")
        docs = []
        for it in r.json().get("items", []):
            d = datetime.strptime(it["date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            if d < since:
                continue
            desc = it.get("description", "").replace("-", " ")
            docs.append(RawDoc(
                connector=self.name, doc_type="filing",
                url=f"https://find-and-update.company-information.service.gov.uk/company/{num}/filing-history",
                title=f"Companies House filing: {desc} ({it.get('type')})", published_at=d,
                regulator="CompaniesHouse", form_type=it.get("type"), filing_id=it.get("transaction_id"),
                publisher="Companies House", raw=it,
            ))
        return docs


# ------------------------------------------------------------------ Indian exchanges (licensed feed required)
class IndianExchangeAnnouncements(Connector):
    name = "india_exchange_announcements"
    countries = ("IN",)

    def enabled(self):
        return False, ("NSE/BSE announcement feeds are not available through a permitted free API "
                       "(both sites block automated access). Plug in a licensed feed here for production.")

    def collect(self, ctx, since):
        return []


# ------------------------------------------------------------------ GDELT news discovery (global)
class Gdelt(Connector):
    name = "gdelt"

    def collect(self, ctx, since):
        days = max(1, min(90, (datetime.now(timezone.utc) - since).days or 1))
        names = [f'"{n}"' for n in dict.fromkeys([ctx.short_name, ctx.legal_name]) if n and len(n) > 3][:2]
        q = names[0] if len(names) == 1 else "(" + " OR ".join(names) + ")"
        import time

        for attempt in range(3):
            r = fetch("https://api.gdeltproject.org/api/v2/doc/doc", params={
                "query": q, "mode": "artlist", "format": "json", "maxrecords": 75,
                "timespan": f"{days}d", "sort": "datedesc",
            }, ttl=1800, headers={"User-Agent": "CompanyIntel/0.1"})
            if r.status_code != 429:
                break
            time.sleep(6 * (attempt + 1))
        if r.status_code != 200 or "json" not in r.content_type:
            raise RuntimeError(f"GDELT unavailable ({r.status_code}): {r.text[:120]}")
        docs = []
        for a in (r.json() or {}).get("articles", []):
            try:
                dt = datetime.strptime(a["seendate"], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
            except Exception:
                dt = None
            docs.append(RawDoc(
                connector=self.name, doc_type="article", url=a["url"], title=a.get("title", "").strip(),
                published_at=dt, publisher=a.get("domain"), language=a.get("language"), raw=a,
            ))
        return docs


# ------------------------------------------------------------------ Google News RSS (personal use only)
class GoogleNewsRss(Connector):
    name = "google_news_rss"

    def enabled(self):
        if not get_settings().enable_google_news_rss:
            return False, "Disabled: Google News RSS terms permit personal, non-commercial use only."
        return True, None

    def collect(self, ctx, since):
        region = {"IN": ("en-IN", "IN"), "GB": ("en-GB", "GB")}.get(ctx.country_code or "", ("en-US", "US"))
        r = fetch("https://news.google.com/rss/search", params={
            "q": f'"{ctx.short_name}"', "hl": region[0], "gl": region[1], "ceid": f"{region[1]}:en",
        }, headers={"User-Agent": "Mozilla/5.0 (compatible; CompanyIntel/0.1)"}, ttl=1800)
        if r.status_code != 200:
            raise RuntimeError(f"Google News HTTP {r.status_code}")
        root = ET.fromstring(r.content)
        docs = []
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            src = item.find("source")
            publisher = src.text if src is not None else None
            src_url = src.get("url") if src is not None else None
            if publisher and title.endswith(" - " + publisher):
                title = title[: -len(publisher) - 3]
            try:
                dt = _utc(parsedate_to_datetime(item.findtext("pubDate")))
            except Exception:
                dt = None
            if dt and dt < since:
                continue
            docs.append(RawDoc(
                connector=self.name, doc_type="article", url=item.findtext("link") or "", title=title,
                published_at=dt, publisher=publisher, raw={"publisher_url": src_url},
            ))
        return docs


# ------------------------------------------------------------------ Brave Search (IR, industry media, LinkedIn snippets)
class BraveSearch(Connector):
    name = "brave_search"

    def enabled(self):
        if not get_settings().brave_search_api_key:
            return False, "BRAVE_SEARCH_API_KEY not set (licensed web search; free tier available)"
        return True, None

    def _search(self, endpoint: str, q: str, freshness: str) -> list[dict]:
        r = fetch(f"https://api.search.brave.com/res/v1/{endpoint}/search",
                  params={"q": q, "count": 20, "freshness": freshness},
                  headers={"X-Subscription-Token": get_settings().brave_search_api_key, "Accept": "application/json"},
                  ttl=3600)
        if r.status_code != 200:
            raise RuntimeError(f"Brave HTTP {r.status_code}")
        data = r.json()
        return data.get("results") or data.get("web", {}).get("results", [])

    def collect(self, ctx, since):
        days = (datetime.now(timezone.utc) - since).days
        fresh = "pd" if days <= 1 else "pw" if days <= 7 else "pm" if days <= 31 else "py"
        name = ctx.short_name
        kw = (ctx.industry_keywords or [ctx.industry or ""])[0]
        queries = [
            ("news", f'"{name}"', "article"),
            ("news", f'"{name}" {kw}'.strip(), "article"),
            ("web", f'"{name}" site:linkedin.com', "social_post"),
            ("web", f'"{name}" investor presentation OR press release', "press_release"),
        ]
        docs = []
        for endpoint, q, dtype in queries:
            for it in self._search(endpoint, q, fresh):
                dt = None
                if it.get("page_age"):
                    try:
                        dt = _utc(datetime.fromisoformat(it["page_age"].replace("Z", "+00:00")))
                    except ValueError:
                        pass
                docs.append(RawDoc(
                    connector=self.name, doc_type=dtype, url=it.get("url", ""),
                    title=html_to_text(it.get("title", "")), snippet=html_to_text(it.get("description", "")),
                    published_at=dt, publisher=(it.get("meta_url") or {}).get("hostname"), raw={"query": q},
                ))
        return docs


ALL_CONNECTORS: list[Connector] = [
    SecEdgar(), CompaniesHouse(), IndianExchangeAnnouncements(), Gdelt(), GoogleNewsRss(), BraveSearch(),
]
