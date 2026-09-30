"""Central configuration. Every value comes from environment variables (see .env.example)."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "dev"  # dev | prod
    database_url: str = "sqlite:///./data/dev.db"
    jwt_secret: str = "change-me-in-production"
    jwt_ttl_hours: int = 24 * 14
    cors_origins: str = "http://localhost:3000"

    # Contact string required by SEC EDGAR fair-access policy ("Company Name email").
    sec_user_agent: str = "CompanyIntel MVP admin@example.com"

    # --- LLM routing (provider:model). Empty key => deterministic, no-LLM mode. ---
    llm_cheap: str = "anthropic:claude-haiku-4-5"
    llm_strong: str = "anthropic:claude-opus-5-5"
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    google_api_key: str = ""

    # --- Optional data providers ---
    brave_search_api_key: str = ""        # licensed web search (IR pages, industry media, LinkedIn snippets)
    companies_house_api_key: str = ""     # UK Companies House (free key)
    openfigi_api_key: str = ""            # optional; raises OpenFIGI rate limits

    # Google News RSS terms restrict use to personal, non-commercial feed readers.
    # Off by default; only enable for private evaluation.
    enable_google_news_rss: bool = False

    # --- Pipeline behaviour ---
    report_ttl_minutes: int = 30          # reuse a report for this long (single-flight cache)
    lookback_days: int = 90
    run_jobs_inline: bool = True          # dev: run jobs in API process threads; prod: separate worker
    daily_refresh_hour_utc: int = 1

    # --- Abuse prevention ---
    rate_limit_per_minute: int = 30
    anon_analyses_per_day: int = 10
    user_analyses_per_day: int = 50

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def llm_enabled(self) -> bool:
        return bool(self.anthropic_api_key or self.openai_api_key or self.google_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
