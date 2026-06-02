"""User preferences — persisted defaults for browser, reliability, and model.

Stored in a small JSON file next to the app. Secrets (API key) are NEVER written
here; the durable source for the key stays `.env` / Streamlit secrets.
"""
import json
from pathlib import Path

from core.config import config

PREFS_PATH = Path(".qa_prefs.json")

DEFAULT_PREFS: dict = {
    # Browser defaults
    "browser":  "chromium",
    "headless": True,
    "timeout":  30000,
    # Reliability defaults
    "max_retries":          0,
    "use_vision":           False,
    "per_step_screenshots": False,
    # Model
    "model": config.CLAUDE_MODEL,
}


def load_prefs() -> dict:
    """Return DEFAULT_PREFS overlaid with any values saved on disk."""
    prefs = dict(DEFAULT_PREFS)
    try:
        if PREFS_PATH.exists():
            data = json.loads(PREFS_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                prefs.update({k: v for k, v in data.items() if k in DEFAULT_PREFS})
    except Exception:
        pass
    return prefs


def save_prefs(values: dict) -> None:
    """Persist only the known preference keys (ignores anything extra)."""
    prefs = {k: values.get(k, DEFAULT_PREFS[k]) for k in DEFAULT_PREFS}
    PREFS_PATH.write_text(json.dumps(prefs, indent=2), encoding="utf-8")
