"""Data model. These SQLAlchemy models are the source of truth; `python -m app.tools.dump_schema`
emits the equivalent PostgreSQL DDL into migrations/schema.sql.

Hierarchy: Country -> Exchange -> Security (instrument) -> Company. Nothing here is India/US/UK specific.
"""
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (
    JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, Numeric, String, Text,
    UniqueConstraint, Index,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base

JSONType = JSON().with_variant(JSONB(), "postgresql")


def _id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def Id():
    return mapped_column(String(36), primary_key=True, default=_id)


def FK(target: str, nullable=True, ondelete="CASCADE"):
    return mapped_column(String(36), ForeignKey(target, ondelete=ondelete), nullable=nullable, index=True)


def Created():
    return mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


# ---------------------------------------------------------------- reference data
class Country(Base):
    __tablename__ = "countries"
    code: Mapped[str] = mapped_column(String(2), primary_key=True)  # ISO 3166-1 alpha-2
    name: Mapped[str] = mapped_column(String(100))
    currency: Mapped[str | None] = mapped_column(String(3))
    timezone: Mapped[str | None] = mapped_column(String(64))
    regulator: Mapped[str | None] = mapped_column(String(100))


class Exchange(Base):
    __tablename__ = "exchanges"
    mic: Mapped[str] = mapped_column(String(10), primary_key=True)  # ISO 10383 MIC, e.g. XNSE, XBOM, XNYS
    code: Mapped[str] = mapped_column(String(20))  # display code: NSE, BSE, NYSE, LSE
    name: Mapped[str] = mapped_column(String(200))
    country_code: Mapped[str] = mapped_column(String(2), ForeignKey("countries.code"))
    figi_exch_code: Mapped[str | None] = mapped_column(String(10))  # OpenFIGI exchCode (IN, IB, US, LN...)


class Sector(Base):
    __tablename__ = "sectors"
    id: Mapped[str] = Id()
    name: Mapped[str] = mapped_column(String(100), unique=True)


class Industry(Base):
    __tablename__ = "industries"
    id: Mapped[str] = Id()
    name: Mapped[str] = mapped_column(String(150), unique=True)
    sector_id: Mapped[str | None] = FK("sectors.id", ondelete="SET NULL")
    # Search keywords for industry media discovery, e.g. ["automotive", "auto components", "EV"]
    keywords: Mapped[list | None] = mapped_column(JSONType)


# ---------------------------------------------------------------- companies & securities
class Company(Base):
    __tablename__ = "companies"
    id: Mapped[str] = Id()
    legal_name: Mapped[str] = mapped_column(String(300), index=True)
    short_name: Mapped[str | None] = mapped_column(String(200))
    aliases: Mapped[list | None] = mapped_column(JSONType)
    country_code: Mapped[str | None] = mapped_column(String(2), ForeignKey("countries.code"))
    industry_id: Mapped[str | None] = FK("industries.id", ondelete="SET NULL")
    industry_text: Mapped[str | None] = mapped_column(String(200))  # raw classification from source
    website: Mapped[str | None] = mapped_column(String(300))
    ir_url: Mapped[str | None] = mapped_column(String(300))
    linkedin_url: Mapped[str | None] = mapped_column(String(300))
    lei: Mapped[str | None] = mapped_column(String(20))
    # External identifiers: {"sec_cik": "0000320193", "companies_house": "00000000", "bse_code": "500240"}
    identifiers: Mapped[dict | None] = mapped_column(JSONType)
    profile: Mapped[dict | None] = mapped_column(JSONType)  # description, address, fiscal year end...
    profile_refreshed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = Created()

    securities: Mapped[list["Security"]] = relationship(back_populates="company", cascade="all, delete-orphan")


class Security(Base):
    __tablename__ = "securities"
    __table_args__ = (UniqueConstraint("exchange_mic", "ticker", name="uq_security_exchange_ticker"),)
    id: Mapped[str] = Id()
    company_id: Mapped[str] = FK("companies.id", nullable=False)
    exchange_mic: Mapped[str] = mapped_column(String(10), ForeignKey("exchanges.mic"))
    ticker: Mapped[str] = mapped_column(String(40), index=True)
    isin: Mapped[str | None] = mapped_column(String(12), index=True)
    figi: Mapped[str | None] = mapped_column(String(12))
    instrument_type: Mapped[str] = mapped_column(String(30), default="common_stock")
    currency: Mapped[str | None] = mapped_column(String(3))
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    company: Mapped[Company] = relationship(back_populates="securities")


class Competitor(Base):
    __tablename__ = "competitors"
    __table_args__ = (UniqueConstraint("company_id", "competitor_company_id"),)
    id: Mapped[str] = Id()
    company_id: Mapped[str] = FK("companies.id", nullable=False)
    competitor_company_id: Mapped[str | None] = FK("companies.id")
    competitor_name: Mapped[str] = mapped_column(String(300))
    basis: Mapped[str | None] = mapped_column(Text)  # why they're considered a competitor
    source_id: Mapped[str | None] = FK("sources.id", ondelete="SET NULL")
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    created_at: Mapped[datetime] = Created()


# ---------------------------------------------------------------- sources & documents
class Source(Base):
    """A publisher/channel (SEC EDGAR, BSE, Reuters, company IR site, company LinkedIn...)."""
    __tablename__ = "sources"
    id: Mapped[str] = Id()
    name: Mapped[str] = mapped_column(String(200))
    domain: Mapped[str | None] = mapped_column(String(200), unique=True)
    source_type: Mapped[str] = mapped_column(String(40))  # regulatory|company|major_media|industry_media|social|community|aggregator
    credibility_tier: Mapped[int] = mapped_column(Integer)  # 1 (regulatory) .. 6 (unverified)
    country_code: Mapped[str | None] = mapped_column(String(2))
    license_notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = Created()


class Document(Base):
    """One retrieved item (filing, article, press release, social post). Dedup by content_hash."""
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("company_id", "content_hash", name="uq_document_hash"),)
    id: Mapped[str] = Id()
    company_id: Mapped[str] = FK("companies.id", nullable=False)
    source_id: Mapped[str | None] = FK("sources.id", ondelete="SET NULL")
    doc_type: Mapped[str] = mapped_column(String(30))  # filing|article|press_release|social_post|presentation
    connector: Mapped[str] = mapped_column(String(40))  # which collector found it
    url: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    snippet: Mapped[str | None] = mapped_column(Text)
    body_text: Mapped[str | None] = mapped_column(Text)  # only stored where licensing allows
    author: Mapped[str | None] = mapped_column(String(200))
    language: Mapped[str | None] = mapped_column(String(10))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    retrieved_at: Mapped[datetime] = Created()
    content_hash: Mapped[str] = mapped_column(String(64))
    credibility_tier: Mapped[int] = mapped_column(Integer, default=6)
    match_confidence: Mapped[float] = mapped_column(Float, default=1.0)  # is it really about this company?
    processed: Mapped[bool] = mapped_column(Boolean, default=False)
    raw: Mapped[dict | None] = mapped_column(JSONType)


class Filing(Base):
    __tablename__ = "filings"
    document_id: Mapped[str] = mapped_column(String(36), ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True)
    regulator: Mapped[str] = mapped_column(String(40))  # SEC, BSE, NSE, RNS, CompaniesHouse
    form_type: Mapped[str | None] = mapped_column(String(40))  # 8-K, 10-Q, "Board Meeting", CS01...
    filing_id: Mapped[str | None] = mapped_column(String(100))  # accession number / announcement id
    items: Mapped[list | None] = mapped_column(JSONType)  # e.g. 8-K item codes
    period_of_report: Mapped[date | None] = mapped_column(Date)


class Article(Base):
    __tablename__ = "articles"
    document_id: Mapped[str] = mapped_column(String(36), ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True)
    publisher: Mapped[str | None] = mapped_column(String(200))
    section: Mapped[str | None] = mapped_column(String(100))
    tone: Mapped[float | None] = mapped_column(Float)


# ---------------------------------------------------------------- events (the core asset)
class Event(Base):
    __tablename__ = "events"
    __table_args__ = (Index("ix_events_company_date", "company_id", "event_date"),)
    id: Mapped[str] = Id()
    company_id: Mapped[str] = FK("companies.id", nullable=False)
    event_type: Mapped[str] = mapped_column(String(50), index=True)  # see pipeline/taxonomy.py
    category: Mapped[str] = mapped_column(String(30))  # financial|business|capital|governance|regulatory|market|industry
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    event_date: Mapped[date | None] = mapped_column(Date)
    announcement_date: Mapped[date | None] = mapped_column(Date)
    detected_at: Mapped[datetime] = Created()
    materiality: Mapped[str] = mapped_column(String(10))  # high|medium|low
    materiality_score: Mapped[float] = mapped_column(Float, default=0)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    # verified_primary | company_claim | corroborated | single_source | unverified
    verification: Mapped[str] = mapped_column(String(20))
    claim_type: Mapped[str] = mapped_column(String(20), default="fact")  # fact|company_claim|external_analysis|unverified
    sentiment: Mapped[str | None] = mapped_column(String(10))  # positive|negative|neutral|mixed (direction for the business)
    impact_areas: Mapped[list | None] = mapped_column(JSONType)
    related_company_ids: Mapped[list | None] = mapped_column(JSONType)
    financial_impact: Mapped[str | None] = mapped_column(Text)
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)  # for cross-run dedup
    extracted_by: Mapped[str] = mapped_column(String(40), default="rules")
    evidence_quote: Mapped[str | None] = mapped_column(Text)

    sources: Mapped[list["EventSource"]] = relationship(cascade="all, delete-orphan")


class EventSource(Base):
    __tablename__ = "event_sources"
    __table_args__ = (UniqueConstraint("event_id", "document_id"),)
    id: Mapped[str] = Id()
    event_id: Mapped[str] = FK("events.id", nullable=False)
    document_id: Mapped[str] = FK("documents.id", nullable=False)
    role: Mapped[str] = mapped_column(String(20), default="supporting")  # primary|supporting|contradicting
    document: Mapped[Document] = relationship()


class IndustryEvent(Base):
    __tablename__ = "industry_events"
    id: Mapped[str] = Id()
    industry_id: Mapped[str | None] = FK("industries.id")
    country_code: Mapped[str | None] = mapped_column(String(2))
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    event_date: Mapped[date | None] = mapped_column(Date)
    document_id: Mapped[str | None] = FK("documents.id", ondelete="SET NULL")
    affected_company_ids: Mapped[list | None] = mapped_column(JSONType)
    materiality: Mapped[str] = mapped_column(String(10), default="low")
    created_at: Mapped[datetime] = Created()


# ---------------------------------------------------------------- financials & market
class FinancialMetric(Base):
    __tablename__ = "financial_metrics"
    __table_args__ = (UniqueConstraint("company_id", "metric", "period_end", "period_type", "source_document_id"),)
    id: Mapped[str] = Id()
    company_id: Mapped[str] = FK("companies.id", nullable=False)
    metric: Mapped[str] = mapped_column(String(60))  # revenue, net_income, ebitda, eps, operating_cash_flow...
    period_type: Mapped[str] = mapped_column(String(10))  # Q|H|FY|TTM
    period_end: Mapped[date] = mapped_column(Date)
    value: Mapped[float] = mapped_column(Numeric(24, 4))
    currency: Mapped[str | None] = mapped_column(String(3))
    unit: Mapped[str | None] = mapped_column(String(20))
    source_document_id: Mapped[str | None] = FK("documents.id", ondelete="SET NULL")
    created_at: Mapped[datetime] = Created()


class MarketData(Base):
    __tablename__ = "market_data"
    __table_args__ = (UniqueConstraint("security_id", "trade_date"),)
    id: Mapped[str] = Id()
    security_id: Mapped[str] = FK("securities.id", nullable=False)
    trade_date: Mapped[date] = mapped_column(Date)
    open: Mapped[float | None] = mapped_column(Numeric(18, 4))
    high: Mapped[float | None] = mapped_column(Numeric(18, 4))
    low: Mapped[float | None] = mapped_column(Numeric(18, 4))
    close: Mapped[float | None] = mapped_column(Numeric(18, 4))
    volume: Mapped[float | None] = mapped_column(Numeric(24, 2))
    provider: Mapped[str | None] = mapped_column(String(40))


# ---------------------------------------------------------------- management promise tracker
class ManagementStatement(Base):
    __tablename__ = "management_statements"
    id: Mapped[str] = Id()
    company_id: Mapped[str] = FK("companies.id", nullable=False)
    document_id: Mapped[str | None] = FK("documents.id", ondelete="SET NULL")
    speaker: Mapped[str | None] = mapped_column(String(200))
    speaker_role: Mapped[str | None] = mapped_column(String(100))
    statement: Mapped[str] = mapped_column(Text)  # verbatim quote
    statement_date: Mapped[date | None] = mapped_column(Date)
    is_forward_looking: Mapped[bool] = mapped_column(Boolean, default=False)
    topic: Mapped[str | None] = mapped_column(String(60))
    created_at: Mapped[datetime] = Created()


class ManagementGuidance(Base):
    __tablename__ = "management_guidance"
    id: Mapped[str] = Id()
    company_id: Mapped[str] = FK("companies.id", nullable=False)
    statement_id: Mapped[str | None] = FK("management_statements.id", ondelete="SET NULL")
    metric: Mapped[str] = mapped_column(String(100))  # capacity, revenue, margin, plant_commissioning...
    target_value: Mapped[str | None] = mapped_column(String(100))
    target_unit: Mapped[str | None] = mapped_column(String(40))
    deadline_text: Mapped[str | None] = mapped_column(String(60))  # "FY27", "Q3 2026"
    deadline_date: Mapped[date | None] = mapped_column(Date)
    given_on: Mapped[date | None] = mapped_column(Date)
    # on_track|completed|progressing|delayed|missed|revised|no_recent_evidence
    status: Mapped[str] = mapped_column(String(30), default="no_recent_evidence")
    superseded_by_id: Mapped[str | None] = FK("management_guidance.id", ondelete="SET NULL")
    created_at: Mapped[datetime] = Created()


class GuidanceTracking(Base):
    """Each evidence update against a guidance item. Status changes only when evidence exists."""
    __tablename__ = "guidance_tracking"
    id: Mapped[str] = Id()
    guidance_id: Mapped[str] = FK("management_guidance.id", nullable=False)
    event_id: Mapped[str | None] = FK("events.id", ondelete="SET NULL")
    document_id: Mapped[str | None] = FK("documents.id", ondelete="SET NULL")
    previous_status: Mapped[str | None] = mapped_column(String(30))
    new_status: Mapped[str] = mapped_column(String(30))
    rationale: Mapped[str] = mapped_column(Text)
    assessed_at: Mapped[datetime] = Created()


# ---------------------------------------------------------------- reports, citations, audio
class CompanyDailyReport(Base):
    __tablename__ = "company_daily_reports"
    __table_args__ = (Index("ix_reports_company_mode_time", "company_id", "mode", "generated_at"),)
    id: Mapped[str] = Id()
    company_id: Mapped[str] = FK("companies.id", nullable=False)
    mode: Mapped[str] = mapped_column(String(20), default="quick")
    window_days: Mapped[int] = mapped_column(Integer, default=7)
    generated_at: Mapped[datetime] = Created()
    data_freshness: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # newest source timestamp
    report: Mapped[dict] = mapped_column(JSONType)  # validated ReportJSON
    voice_script: Mapped[str | None] = mapped_column(Text)
    synthesizer: Mapped[str] = mapped_column(String(60))  # "rules" or "anthropic:claude-..."
    event_ids: Mapped[list | None] = mapped_column(JSONType)
    stats: Mapped[dict | None] = mapped_column(JSONType)  # sources searched, docs found, dropped claims...


class Citation(Base):
    __tablename__ = "citations"
    id: Mapped[str] = Id()
    report_id: Mapped[str] = FK("company_daily_reports.id", nullable=False)
    section: Mapped[str] = mapped_column(String(40))
    item_index: Mapped[int] = mapped_column(Integer)
    document_id: Mapped[str] = FK("documents.id", nullable=False)
    event_id: Mapped[str | None] = FK("events.id", ondelete="SET NULL")


class AudioReport(Base):
    __tablename__ = "audio_reports"
    id: Mapped[str] = Id()
    report_id: Mapped[str] = FK("company_daily_reports.id", nullable=False)
    provider: Mapped[str] = mapped_column(String(40))
    voice: Mapped[str | None] = mapped_column(String(40))
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    storage_path: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = Created()


# ---------------------------------------------------------------- users & engagement
class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = Id()
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str | None] = mapped_column(String(200))
    display_name: Mapped[str | None] = mapped_column(String(100))
    preferred_mode: Mapped[str] = mapped_column(String(20), default="quick")
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = Created()
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Watchlist(Base):
    __tablename__ = "watchlists"
    id: Mapped[str] = Id()
    user_id: Mapped[str] = FK("users.id", nullable=False)
    name: Mapped[str] = mapped_column(String(100), default="My Companies")
    created_at: Mapped[datetime] = Created()
    items: Mapped[list["WatchlistItem"]] = relationship(cascade="all, delete-orphan")


class WatchlistItem(Base):
    __tablename__ = "watchlist_items"
    __table_args__ = (UniqueConstraint("watchlist_id", "company_id"),)
    id: Mapped[str] = Id()
    watchlist_id: Mapped[str] = FK("watchlists.id", nullable=False)
    company_id: Mapped[str] = FK("companies.id", nullable=False)
    added_at: Mapped[datetime] = Created()
    company: Mapped[Company] = relationship()


class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[str] = Id()
    user_id: Mapped[str] = FK("users.id", nullable=False)
    company_id: Mapped[str | None] = FK("companies.id")
    event_types: Mapped[list | None] = mapped_column(JSONType)  # null = any
    min_materiality: Mapped[str] = mapped_column(String(10), default="high")
    channel: Mapped[str] = mapped_column(String(20), default="web")  # web|email|telegram|whatsapp|push
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_triggered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = Created()


class ResearchQuery(Base):
    __tablename__ = "research_queries"
    id: Mapped[str] = Id()
    user_id: Mapped[str | None] = FK("users.id", ondelete="SET NULL")
    client_key: Mapped[str | None] = mapped_column(String(64), index=True)  # hashed IP for anon quota
    raw_query: Mapped[str] = mapped_column(Text)
    input_mode: Mapped[str] = mapped_column(String(10), default="text")  # text|voice
    parsed: Mapped[dict | None] = mapped_column(JSONType)
    company_id: Mapped[str | None] = FK("companies.id", ondelete="SET NULL")
    report_id: Mapped[str | None] = FK("company_daily_reports.id", ondelete="SET NULL")
    job_id: Mapped[str | None] = mapped_column(String(36))
    triggered_research: Mapped[bool] = mapped_column(Boolean, default=False)  # counts toward quota (cache hits don't)
    created_at: Mapped[datetime] = Created()


# ---------------------------------------------------------------- job queue (Postgres-backed, no Redis needed)
class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = Id()
    kind: Mapped[str] = mapped_column(String(40))  # analyze_company | daily_refresh
    dedupe_key: Mapped[str | None] = mapped_column(String(200), unique=True)
    payload: Mapped[dict] = mapped_column(JSONType)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)  # queued|running|done|failed
    progress: Mapped[list | None] = mapped_column(JSONType)  # human-readable step log
    result: Mapped[dict | None] = mapped_column(JSONType)
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = Created()
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
