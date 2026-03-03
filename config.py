import os
import logging
from dotenv import load_dotenv

load_dotenv()


class Config:
    # Claude API
    CLAUDE_API_KEY = os.getenv("CLAUDE_API_KEY")

    # Azure Storage
    AZURE_STORAGE_CONNECTION_STRING = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    AZURE_CONTAINER_NAME = os.getenv("AZURE_CONTAINER_NAME", "test-evidence")

    # Playwright
    PLAYWRIGHT_TIMEOUT = int(os.getenv("PLAYWRIGHT_TIMEOUT", "30000"))
    PLAYWRIGHT_HEADLESS = os.getenv("PLAYWRIGHT_HEADLESS", "false").lower() == "true"

    # Application
    MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
    SCREENSHOTS_DIR = os.getenv("SCREENSHOTS_DIR", "screenshots")
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

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
