"""Application configuration loaded from environment variables."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central settings object. Values are read from environment variables or .env file."""

    # Application
    app_env: str = "development"
    public_api_url: str = "http://localhost:8000"
    log_level: str = "DEBUG"

    # CORS — comma-separated origins, or "*" for development
    cors_origins: str = "*"

    # Supabase
    supabase_url: str = ""
    supabase_service_role_key: str = ""
    supabase_anon_key: str | None = None

    # OpenAI
    openai_api_key: str | None = None

    # QStash (production job processing)
    qstash_url: str = "https://qstash-us-east-1.upstash.io"
    qstash_token: str | None = None
    qstash_current_signing_key: str | None = None
    qstash_next_signing_key: str | None = None

    # Temp storage cleanup
    temp_storage_max_age_hours: int = 24

    # Research budgets
    photographer_max_queries: int = 12
    photographer_max_sources: int = 18
    photographer_min_official_sources: int = 3
    photographer_max_per_domain: int = 4

    historian_max_queries: int = 14
    historian_max_sources: int = 20
    historian_min_official_sources: int = 4
    historian_max_per_domain: int = 4

    # Processing
    research_timeout_seconds: int = 180
    max_episode_minutes: int = 20
    min_episode_minutes: int = 3

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def is_development(self) -> bool:
        return self.app_env == "development"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """Return cached settings instance."""
    return Settings()
