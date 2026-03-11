import os
import logging
import streamlit as st
from dotenv import load_dotenv

# Load local .env if present
load_dotenv()

def get_secret(key: str, default=None):
    """Fetch secret from Streamlit Cloud secrets or local .env / environment."""
    try:
        if key in st.secrets:          # Streamlit Cloud deployment
            return st.secrets[key]
    except Exception:
        pass                           # No secrets.toml on local dev — fall through
    return os.getenv(key, default)     # Local development via .env


def _build_database_url() -> str | None:
    """
    Resolve the PostgreSQL connection URL.
    Prefers a complete DATABASE_URL; falls back to assembling one from
    individual PG* variables (Azure Flexible Server convention).
    Kept as a module-level function so no credential fragments are left
    as class attributes on Config.
    """
    import urllib.parse
    url = get_secret("DATABASE_URL")
    if url:
        return url
    host = get_secret("PGHOST")
    user = get_secret("PGUSER")
    pwd  = get_secret("PGPASSWORD")
    if host and user and pwd:
        db   = get_secret("PGDATABASE", "postgres")
        port = get_secret("PGPORT", "5432")
        return (
            f"postgres://{user}:{urllib.parse.quote_plus(pwd)}"
            f"@{host}:{port}/{db}?sslmode=require"
        )
    return None


class Config:
    # Claude API
    CLAUDE_API_KEY = get_secret("CLAUDE_API_KEY")

    # Azure Storage
    AZURE_STORAGE_CONNECTION_STRING = get_secret("AZURE_STORAGE_CONNECTION_STRING")
    AZURE_CONTAINER_NAME = get_secret("AZURE_CONTAINER_NAME", "test-evidence")

    # PostgreSQL (Azure-hosted) — assembled without leaving credential fragments as attrs
    DATABASE_URL = _build_database_url()

    # Playwright
    PLAYWRIGHT_TIMEOUT = int(get_secret("PLAYWRIGHT_TIMEOUT", "30000"))
    PLAYWRIGHT_HEADLESS = str(get_secret("PLAYWRIGHT_HEADLESS", "false")).lower() == "true"

    # Application
    MAX_RETRIES = int(get_secret("MAX_RETRIES", "3"))
    SCREENSHOTS_DIR = get_secret("SCREENSHOTS_DIR", "screenshots")
    LOG_LEVEL = get_secret("LOG_LEVEL", "INFO")

    # Rate limiting / cost controls
    MAX_REQUIREMENTS = int(get_secret("MAX_REQUIREMENTS", "20"))       # Max requirements per analysis
    MAX_TEST_CASES   = int(get_secret("MAX_TEST_CASES",   "10"))       # Max test cases per generation
    MAX_API_CALLS_PER_SESSION = int(get_secret("MAX_API_CALLS_PER_SESSION", "50"))  # Soft cap warning

    @classmethod
    def validate(cls):
        if not cls.CLAUDE_API_KEY:
            raise ValueError("CLAUDE_API_KEY environment variable is required")
        return True


def configure_logging():
    """Configure root logging once at application startup."""
    level = getattr(logging, Config.LOG_LEVEL.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


config = Config()