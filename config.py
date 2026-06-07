"""
config.py
=========
All configuration for the project lives here.

We use pydantic-settings (v2) to:
  • Read values from environment variables (and a .env file during dev)
  • Provide type-safe defaults
  • Fail loudly if a REQUIRED variable (like Supabase credentials) is missing

Why centralise config?
  If you hard-code API URLs or credentials inside scraper.py you have to
  hunt through multiple files when something changes. One config module
  means one place to look.

Environment variables (set in GitHub Actions Secrets for production):
  SUPABASE_URL       – Your project URL, e.g. https://xxxx.supabase.co
  SUPABASE_KEY       – service_role key (keeps full write access)
  LOG_LEVEL          – DEBUG | INFO | WARNING | ERROR  (default: INFO)
"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Pydantic model that maps environment variables → typed Python attributes.

    Fields marked with no default are REQUIRED — the app will raise a
    ValidationError at startup if they are missing, giving you a clear
    error message instead of a cryptic AttributeError later.
    """

    model_config = SettingsConfigDict(
        # Automatically load a .env file when running locally.
        # In GitHub Actions the variables come from Secrets, not a file.
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,       # SUPABASE_URL == supabase_url
        extra="ignore",             # ignore unknown env vars silently
    )

    # ------------------------------------------------------------------
    # RemoteOK API settings
    # ------------------------------------------------------------------
    API_URL: str = Field(
        default="https://remoteok.com/api",
        description="Base URL of the RemoteOK JSON API",
    )
    USER_AGENT: str = Field(
        default=(
            "Mozilla/5.0 (compatible; RemoteOK-Pipeline/1.0; "
            "+https://github.com/your-username/remoteok-pipeline)"
        ),
        description="HTTP User-Agent header sent with each request",
    )
    REQUEST_TIMEOUT: int = Field(
        default=30,
        description="Seconds before an HTTP request is abandoned",
    )
    MAX_RETRIES: int = Field(
        default=3,
        description="How many times to retry a failed HTTP request",
    )

    # ------------------------------------------------------------------
    # Supabase credentials  (REQUIRED – no defaults)
    # ------------------------------------------------------------------
    SUPABASE_URL: str = Field(
        ...,          # '...' means required in Pydantic
        description="Supabase project URL (Settings → API in the dashboard)",
    )
    SUPABASE_KEY: str = Field(
        ...,
        description="Supabase service_role secret key",
    )
    SUPABASE_TABLE: str = Field(
        default="remote_jobs",
        description="Name of the Supabase table to upsert into",
    )

    # ------------------------------------------------------------------
    # General
    # ------------------------------------------------------------------
    LOG_LEVEL: str = Field(
        default="INFO",
        description="Python logging level: DEBUG | INFO | WARNING | ERROR",
    )
    BATCH_SIZE: int = Field(
        default=100,
        description="Number of records sent to Supabase in a single upsert call",
    )


# Singleton instance – import this everywhere instead of instantiating Settings()
# repeatedly.
#
#   from config import settings
#   print(settings.API_URL)
settings = Settings()
