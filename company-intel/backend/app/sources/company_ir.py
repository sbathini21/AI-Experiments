"""Company investor-relations connector (tier 2 — company primary source).

Why this matters: Indian listed companies must publish exchange disclosures on their own website
(SEBI LODR Regulation 46), US/UK issuers publish press releases and results on IR pages. This is a
permitted, primary source where exchange feeds are licensed.

Politeness: honours robots.txt for every URL, fetches at most a handful of pages per run, only downloads
PDFs that robots.txt allows, and keeps just the first pages' text for extraction.
"""
import io
import logging
import re
import urllib.robotparser
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

from .base import CompanyContext, Connector, RawDoc
from .connectors import html_to_text
from .http import fetch

log = logging.getLogger(__name__)

UA = "Mozilla/5.0 (compatible; CompanyIntel/0.1; +research)"
IR_LINK = re.compile(r"press|release|disclos|announce|investor|financial|results|news|stock exchange|shareholder|intimation|outcome|board meeting|media", re.I)
SKIP_LINK = re.compile(r"contact|career|privacy|policy|terms|login|cookie|sitemap|grievance|memorandum|article-of-association|code-of-conduct|form-isr", re.I)
GENERIC = re.compile(r"^(view|download|click here|read more|pdf|here|open|details?)$", re.I)
MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
A_RX = re.compile(r"<a\b[^>]*href=[\"']([^\"'#]+)[\"'][^>]*>(.*?)</a>", re.I | re.S)
ROW_RX = re.compile(r"<(tr|li|p|div)\b[^>]*>((?:(?!<(?:tr|li)\b).)*?)</\1>", re.I | re.S)


def parse_date(text: str) -> tuple[datetime | None, str]:
    t = text.replace("_", "-")
    m = re.search(r"(?<!\d)(\d{1,2})[.\-/ ](\d{1,2})[.\-/ ](20\d\d)(?!\d)", t)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return datetime(y, mo, d, tzinfo=timezone.utc), "day"
    m = re.search(r"(?<!\d)(\d{1,2})(?:st|nd|rd|th)?[\s.\-]*(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?[\s.\-,]*(20\d\d)", t, re.I)
    if m:
        try:
            return datetime(int(m.group(3)), MONTHS[m.group(2).lower()], int(m.group(1)), tzinfo=timezone.utc), "day"
        except ValueError:
            pass
    m = re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(20\d\d)", t, re.I)
    if m:
        try:
            return datetime(int(m.group(3)), MONTHS[m.group(1).lower()], int(m.group(2)), tzinfo=timezone.utc), "day"
        except ValueError:
            pass
    m = re.search(r"(?<!\d)(20\d\d)(0[1-9]|1[0-2])([0-3]\d)(?!\d)", t)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc), "day"
        except ValueError:
            pass
    m = re.search(r"/uploads/(20\d\d)/(0[1-9]|1[0-2])/", t)  # WordPress upload folder
    if m:
        return datetime(int(m.group(1)), int(m.group(2)), 1, tzinfo=timezone.utc), "month"
    return None, "none"


def headline_from_filing_text(text: str, allow_fallback: bool = True) -> str | None:
    """Pull the real headline out of an exchange cover letter / press release PDF."""
    t = re.sub(r"\s+", " ", text)
    for rx in [r"titled\s*[\u201c\"']\s*(.{12,220}?)\s*[\u201d\"']",
               r"Press Release\s*[-\u2013:]\s*(.{12,200}?)\s+(?:This is|Kindly|We request|Thanking|Dear)",
               r"Subject\s*:\s*(.{8,220}?)\s+(?:Dear|Respected|Ref\b|Sir|Madam)"]:
        m = re.search(rx, t, re.I)
        if m:
            h = m.group(1).strip(" -\u2013:.")
            h = re.sub(r"^(Press Release\s*[-\u2013:]\s*)", "", h, flags=re.I)
            if not re.fullmatch(r"(?i)(a )?press release|intimation|disclosure|outcome of board meeting", h):
                return h[:220]
    # Cover letter without a title: take the first substantive sentence after the signature block.
    m = re.search(r"Compliance Officer(.{40,1500})", t, re.I) if allow_fallback else None
    if m:
        for sent in re.split(r"(?<=[.!?])\s+", m.group(1)):
            words = sent.split()
            if 8 <= len(words) <= 45 and not re.search(r"(?i)registered address|CIN:|Email|Website|Encl|digitally signed|signature", sent):
                return sent.strip()[:220]
    return None


def letter_body(text: str) -> str:
    """Drop letterhead/address boilerplate: keep what follows the salutation if there is one."""
    m = re.search(r"(?i)Dear\s+Sir\s*/?\s*Madam,?|Dear\s+Sir,?|Respected\s+Sir,?", text)
    return text[m.end():].strip() if m else text


def is_exchange_cover_letter(text: str) -> bool:
    return bool(re.search(r"(?i)(BSE Limited|National Stock Exchange|Listing Department|Corporate Relationship Department)", text[:1500])
                and re.search(r"(?i)Regulation 30|Listing Obligations|Scrip Code", text[:2500]))


def title_from_filename(url: str) -> str:
    name = urlparse(url).path.rsplit("/", 1)[-1]
    name = re.sub(r"\.(pdf|html?|aspx?)$", "", name, flags=re.I)
    name = re.sub(r"[_\-]+", " ", name).strip()
    return name[:1].upper() + name[1:] if name else url


class CompanyIR(Connector):
    name = "company_ir"
    MAX_PAGES = 5
    MAX_PDFS = 8

    def applies(self, ctx: CompanyContext) -> bool:
        return bool(ctx.website_domains)

    def _robots(self, base: str) -> urllib.robotparser.RobotFileParser:
        rp = urllib.robotparser.RobotFileParser()
        r = fetch(urljoin(base, "/robots.txt"), ttl=86400, headers={"User-Agent": UA})
        rp.parse(r.text.splitlines() if r.status_code == 200 else [])
        return rp

    def collect(self, ctx: CompanyContext, since: datetime) -> list[RawDoc]:
        base = f"https://{ctx.website_domains[0]}/"
        rp = self._robots(base)
        allowed = lambda u: rp.can_fetch(UA, u)  # noqa: E731
        home = fetch(base, ttl=3600, headers={"User-Agent": UA})
        if home.status_code != 200:
            raise RuntimeError(f"website HTTP {home.status_code}")
        dom = ctx.website_domains[0]
        pages = []
        for href, label in A_RX.findall(home.text):
            url = urljoin(base, href.strip())
            text = html_to_text(label)
            if dom not in (urlparse(url).hostname or "") or url.lower().endswith(".pdf"):
                continue
            if IR_LINK.search(text + " " + url) and not SKIP_LINK.search(text + " " + url) and url not in pages and allowed(url):
                pages.append(url)
        # Most specific pages first (press releases, disclosures, results)
        pages.sort(key=lambda u: 0 if re.search(r"press|disclos|announce|result|intimation|news", u, re.I) else 1)
        docs, seen, pdfs = [], set(), 0
        for page in pages[: self.MAX_PAGES]:
            r = fetch(page, ttl=3600, headers={"User-Agent": UA})
            if r.status_code != 200:
                continue
            html = r.text
            prev_end = 0
            for am in A_RX.finditer(html):
                href, label = am.group(1), am.group(2)
                # Context for generic "View" links: the enclosing table row if any, else text since the previous link.
                start = am.start()
                tr = html.rfind("<tr", 0, start)
                tr_end = html.rfind("</tr>", 0, start)
                ctx_html = html[tr:start] if tr != -1 and tr > tr_end else html[prev_end:start][-600:]
                prev_end = am.end()
                url = urljoin(page, href.strip())
                if url in seen or not re.search(r"\.pdf($|\?)", url, re.I) or SKIP_LINK.search(url):
                    continue
                seen.add(url)
                text = html_to_text(label)
                if not text or GENERIC.match(text):
                    text = re.sub(r"\b(view|download)\b", "", html_to_text(ctx_html, 300), flags=re.I).strip(" |-")
                    text = re.sub(r"^(20\d\d[\s\-]*)+", "", text).strip()  # drop leading year columns
                fname = title_from_filename(url)
                ftoks = {w for w in re.findall(r"[a-z]{4,}", fname.lower())} - {"press", "release", "signed", "final"}
                if text and ftoks and not ftoks & set(re.findall(r"[a-z]{4,}", text.lower())):
                    text = fname  # context text doesn't match the file: trust the filename over a possibly misaligned label
                if not text or len(text) < 6 or GENERIC.match(text):
                    text = fname
                dt, precision = parse_date(f"{text} {url}")
                if not dt or dt < since or "brochure" in text.lower():
                    continue
                body = None
                if pdfs < self.MAX_PDFS and url not in ctx.known_urls and allowed(url):
                    body = self._pdf_text(url)
                    pdfs += 1
                title = text if len(text) > 12 else f"{text} ({dt.strftime('%d %b %Y')})"
                cover = bool(body and is_exchange_cover_letter(body))
                if body:
                    generic_title = bool(re.fullmatch(r"(?i)(kel\s+)?press release.*", title)) or len(title) < 30
                    h = headline_from_filing_text(body, allow_fallback=generic_title)
                    if h and (generic_title or re.search(r"(?i)^(\d{4}\s*-\s*\d{4}\s+)?(intimation|disclosure)", title)):
                        title = h
                    m_date = re.search(r"Date\s*:\s*([^A-Z]{0,4}[\w ,.]{6,25}?20\d\d)", body[:1500])
                    if m_date:
                        d2, prec2 = parse_date(m_date.group(1))
                        if d2:
                            dt, precision = d2, prec2  # the letter's own date beats the listing/upload date
                if dt < since:
                    continue
                docs.append(RawDoc(
                    connector=self.name, doc_type="press_release" if re.search(r"press", url + text, re.I) else "filing",
                    url=url, title=title, published_at=dt, snippet=letter_body(body)[:500] if body else None, body_text=body,
                    publisher=dom, raw={"date_precision": precision, "listed_on": page, "exchange_filing_copy": cover},
                ))
        return docs

    def _pdf_text(self, url: str) -> str | None:
        try:
            r = fetch(url, ttl=86400, timeout=40, headers={"User-Agent": UA})
            if r.status_code != 200 or len(r.content) > 8_000_000:
                return None
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(r.content))
            text = " ".join((p.extract_text() or "") for p in reader.pages[:3])
            text = re.sub(r"\s+", " ", text).strip()
            return text[:15000] or None
        except Exception as e:
            log.info("pdf extract failed %s: %s", url, e)
            return None
