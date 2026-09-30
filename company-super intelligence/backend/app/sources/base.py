"""Connector contract. Each connector turns a company context into RawDocs; the pipeline does the rest."""
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class CompanyContext:
    company_id: str
    legal_name: str
    short_name: str
    aliases: list[str]
    country_code: str | None
    tickers: list[tuple[str, str]]  # (exchange_code, ticker)
    identifiers: dict
    industry: str | None
    industry_keywords: list[str]
    website_domains: list[str]
    linkedin_url: str | None = None
    known_urls: set[str] = field(default_factory=set)  # already-stored URLs (skip re-downloading bodies)


@dataclass
class RawDoc:
    connector: str
    doc_type: str  # filing|article|press_release|social_post|presentation
    url: str
    title: str
    published_at: datetime | None
    snippet: str | None = None
    body_text: str | None = None
    author: str | None = None
    publisher: str | None = None
    language: str | None = None
    # Filing-specific
    regulator: str | None = None
    form_type: str | None = None
    filing_id: str | None = None
    items: list[str] = field(default_factory=list)
    # Hints from connectors that know the event type (e.g. SEC 8-K items)
    event_type_hint: str | None = None
    raw: dict | None = None


@dataclass
class ConnectorResult:
    name: str
    docs: list[RawDoc]
    status: str = "ok"  # ok|skipped|error
    note: str | None = None


class Connector:
    name = "base"
    countries: tuple[str, ...] | None = None  # None = all markets

    def applies(self, ctx: CompanyContext) -> bool:
        return self.countries is None or ctx.country_code in self.countries

    def enabled(self) -> tuple[bool, str | None]:
        return True, None

    def collect(self, ctx: CompanyContext, since: datetime) -> list[RawDoc]:
        raise NotImplementedError
