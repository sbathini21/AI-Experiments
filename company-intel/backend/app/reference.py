"""Static reference data: markets, exchanges and the source-credibility registry.

Adding a market = adding rows here plus (optionally) a regulatory connector in app/sources/.
"""
from urllib.parse import urlparse

COUNTRIES = [
    # code, name, currency, timezone, regulator
    ("IN", "India", "INR", "Asia/Kolkata", "SEBI"),
    ("US", "United States", "USD", "America/New_York", "SEC"),
    ("GB", "United Kingdom", "GBP", "Europe/London", "FCA"),
]

EXCHANGES = [
    # mic, code, name, country, OpenFIGI exchCode
    ("XNSE", "NSE", "National Stock Exchange of India", "IN", "IS"),
    ("XBOM", "BSE", "BSE Limited", "IN", "IB"),
    ("XNYS", "NYSE", "New York Stock Exchange", "US", "UN"),
    ("XNAS", "NASDAQ", "Nasdaq", "US", "UW"),
    ("XASE", "NYSE American", "NYSE American", "US", "UA"),
    ("ARCX", "NYSE Arca", "NYSE Arca", "US", "UP"),
    ("OTCM", "OTC", "OTC Markets", "US", "UV"),
    ("XLON", "LSE", "London Stock Exchange", "GB", "LN"),
]

# OpenFIGI composite codes that map to a country but not one venue.
FIGI_COMPOSITE = {"US": "US", "IN": "IN", "LN": "GB"}

EXCHANGE_BY_FIGI = {e[4]: e[0] for e in EXCHANGES}

# (domain, name, source_type, tier, license_notes)
SOURCE_REGISTRY = [
    # Tier 1 — primary regulatory / exchange
    ("sec.gov", "SEC EDGAR", "regulatory", 1, "US public domain; fair-access policy: <=10 req/s with User-Agent."),
    ("nseindia.com", "NSE India", "regulatory", 1, "Website terms restrict automated access; use licensed feed in production."),
    ("bseindia.com", "BSE India", "regulatory", 1, "Website terms restrict automated access; use licensed feed in production."),
    ("sebi.gov.in", "SEBI", "regulatory", 1, None),
    ("londonstockexchange.com", "LSE / RNS", "regulatory", 1, "RNS content is licensed by LSEG."),
    ("fca.org.uk", "FCA", "regulatory", 1, None),
    ("data.fca.org.uk", "FCA National Storage Mechanism", "regulatory", 1, None),
    ("find-and-update.company-information.service.gov.uk", "Companies House", "regulatory", 1, "UK OGL v3."),
    ("api.company-information.service.gov.uk", "Companies House API", "regulatory", 1, "UK OGL v3."),
    # Tier 3 — major financial media
    ("reuters.com", "Reuters", "major_media", 3, None),
    ("bloomberg.com", "Bloomberg", "major_media", 3, None),
    ("ft.com", "Financial Times", "major_media", 3, None),
    ("wsj.com", "Wall Street Journal", "major_media", 3, None),
    ("cnbc.com", "CNBC", "major_media", 3, None),
    ("marketwatch.com", "MarketWatch", "major_media", 3, None),
    ("finance.yahoo.com", "Yahoo Finance", "major_media", 3, None),
    ("economictimes.indiatimes.com", "Economic Times", "major_media", 3, None),
    ("moneycontrol.com", "Moneycontrol", "major_media", 3, None),
    ("livemint.com", "Mint", "major_media", 3, None),
    ("business-standard.com", "Business Standard", "major_media", 3, None),
    ("thehindubusinessline.com", "BusinessLine", "major_media", 3, None),
    ("financialexpress.com", "Financial Express", "major_media", 3, None),
    ("cnbctv18.com", "CNBC-TV18", "major_media", 3, None),
    ("theguardian.com", "The Guardian", "major_media", 3, None),
    ("investing.com", "Investing.com", "major_media", 3, None),
    ("barrons.com", "Barron's", "major_media", 3, None),
    # Tier 4 — industry publications (seed list; discovery adds more with tier 4 by default for known trade press)
    ("autocarpro.in", "Autocar Professional", "industry_media", 4, None),
    ("autocarindia.com", "Autocar India", "industry_media", 4, None),
    ("etauto.com", "ET Auto", "industry_media", 4, None),
    ("auto.economictimes.indiatimes.com", "ET Auto", "industry_media", 4, None),
    ("rushlane.com", "Rushlane", "industry_media", 4, None),
    ("autonews.com", "Automotive News", "industry_media", 4, None),
    ("eetimes.com", "EE Times", "industry_media", 4, None),
    ("semiengineering.com", "Semiconductor Engineering", "industry_media", 4, None),
    ("fiercepharma.com", "Fierce Pharma", "industry_media", 4, None),
    ("pharmabiz.com", "Pharmabiz", "industry_media", 4, None),
    ("techcrunch.com", "TechCrunch", "industry_media", 4, None),
    ("theverge.com", "The Verge", "industry_media", 4, None),
    ("americanbanker.com", "American Banker", "industry_media", 4, None),
    ("manufacturingtodayindia.com", "Manufacturing Today India", "industry_media", 4, None),
    # Tier 5 — company social
    ("linkedin.com", "LinkedIn", "social", 5, "No scraping (User Agreement). Only public search snippets / official APIs."),
    ("x.com", "X", "social", 5, None),
    ("twitter.com", "X", "social", 5, None),
    ("youtube.com", "YouTube", "social", 5, None),
    # Tier 6 — community / aggregators of unknown provenance
    ("reddit.com", "Reddit", "community", 6, None),
    ("stocktwits.com", "Stocktwits", "community", 6, None),
    ("valuepickr.com", "ValuePickr forum", "community", 6, None),
]

TIER_LABELS = {
    1: "Primary regulatory",
    2: "Company primary source",
    3: "Major financial media",
    4: "Industry publication",
    5: "Company social media",
    6: "Unverified / community",
}

# Unknown news domains default here: better than tier 6 because they are published news outlets
# (GDELT only indexes news sites), but below curated major media.
DEFAULT_NEWS_TIER = 4


def domain_of(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def lookup_tier(url: str, company_domains: list[str] | None = None) -> tuple[int, str, str]:
    """Return (tier, source_type, source_name) for a URL. Company-owned domains are tier 2."""
    d = domain_of(url)
    for cd in company_domains or []:
        if cd and (d == cd or d.endswith("." + cd)):
            return 2, "company", d
    best = None
    for dom, name, stype, tier, _ in SOURCE_REGISTRY:
        if d == dom or d.endswith("." + dom):
            if best is None or len(dom) > len(best[0]):
                best = (dom, name, stype, tier)
    if best:
        return best[3], best[2], best[1]
    return DEFAULT_NEWS_TIER, "news", d
