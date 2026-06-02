import sys
import asyncio
import subprocess
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import os

import streamlit as st

from core.config import config, configure_logging
from core.session_state import init as _init_state, state
from ui.theme import inject_theme
from ui.context import build_context
from ui.sidebar import render_sidebar
from ui.pages import overview, run_test, history, preferences

# Configure logging once at startup
configure_logging()

st.set_page_config(page_title="Quiv Agent", page_icon=":material/science:", layout="wide")


# ── Streamlit Cloud: install Playwright browser binary on first boot ────────
@st.cache_resource(show_spinner="Installing browser — first run only, please wait…")
def _install_playwright_browser():
    if os.environ.get("SKIP_BROWSER_INSTALL", "").lower() in ("1", "true", "yes"):
        return 0
    result = subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        st.warning(f"Browser install issue: {result.stderr[:300]}", icon=":material/warning:")
    return result.returncode

_install_playwright_browser()


# ── Startup: clean up orphaned screenshots older than 24 hours ─────────────
@st.cache_resource(show_spinner=False)
def _cleanup_orphaned_screenshots():
    import time
    screenshots_dir = config.SCREENSHOTS_DIR
    if not os.path.exists(screenshots_dir):
        return
    cutoff = time.time() - 24 * 3600
    deleted = 0
    try:
        for filename in os.listdir(screenshots_dir):
            filepath = os.path.join(screenshots_dir, filename)
            if os.path.isfile(filepath) and os.path.getmtime(filepath) < cutoff:
                try:
                    os.remove(filepath)
                    deleted += 1
                except Exception:
                    pass
    except Exception:
        pass
    if deleted:
        import logging as _logging
        _logging.getLogger(__name__).info("Startup: removed %d orphaned screenshot(s)", deleted)

_cleanup_orphaned_screenshots()


# ── Theme + state + services ────────────────────────────────────────────────
inject_theme()
_init_state()

ctx = build_context()
if not st.session_state.get("ready") or ctx is None:
    st.stop()

# ── Sidebar navigation ──────────────────────────────────────────────────────
render_sidebar(ctx)

# ── Page dispatch ───────────────────────────────────────────────────────────
PAGES = {
    "Overview":     overview.render,
    "Run New Test": run_test.render,
    "Run History":  history.render,
    "Preferences":  preferences.render,
}
PAGES.get(state.page, overview.render)(ctx)

# ── Footer ──────────────────────────────────────────────────────────────────
st.markdown(
    '<hr style="border:none;border-top:0.5px solid #E8E8E4;margin:40px 0 16px 0"/>'
    '<p style="font-size:12px;color:#6B7280;text-align:center">Quiv Agent — VG Platform</p>',
    unsafe_allow_html=True,
)
