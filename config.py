import os
import logging
import streamlit as st
from dotenv import load_dotenv

# Load local .env if present
load_dotenv()

def get_secret(key: str, default=None):
    """Fetch secret from Streamlit Cloud or local .env."""
    if key in st.secrets:   # Cloud deployment
        return st.secrets[key]
    return os.getenv(key, default)  # Local development


class Config:
    # Claude API
    CLAUDE_API_KEY = get_secret("CLAUDE_API_KEY")

    # Azure Storage
    AZURE_STORAGE_CONNECTION_STRING = get_secret("AZURE_STORAGE_CONNECTION_STRING")
    AZURE_CONTAINER_NAME = get_secret("AZURE_CONTAINER_NAME", "test-evidence")

    # Playwright
    PLAYWRIGHT_TIMEOUT = int(get_secret("PLAYWRIGHT_TIMEOUT", "30000"))
    PLAYWRIGHT_HEADLESS = str(get_secret("PLAYWRIGHT_HEADLESS", "false")).lower() == "true"

    # Application
    MAX_RETRIES = int(get_secret("MAX_RETRIES", "3"))
    SCREENSHOTS_DIR = get_secret("SCREENSHOTS_DIR", "screenshots")
    LOG_LEVEL = get_secret("LOG_LEVEL", "INFO")

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