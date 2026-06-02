import sys
import asyncio
import subprocess
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import copy
import json
import os
import uuid
from collections import defaultdict
from datetime import datetime

import pandas as pd
import streamlit as st

from core.config import config, configure_logging
from models import PlaywrightConfig, TestCase as _TC, TestStep as _TS
from services.playwright_executor import get_metrics, inspect_dom
from repositories.azure_storage import AzureStorageManager, LocalStorageManager
from repositories.db import DatabaseManager
from services.llm_processor import LLMProcessor

from core.session_state import init as _init_state, state
from services.workflow_service import WorkflowService
from controllers.execution_controller import ExecutionController
from services.report_service import ReportService

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

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400&display=swap');
@import url('https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:opsz,wght,FILL,GRAD@20..48,400,0,0&display=swap');

:root {
    --color-fg:      #000000;
    --color-bg:      #FFFFFF;
    --color-surface: #F5F5F4;
    --color-border:  #E8E8E4;
    --color-muted:   #6B7280;
    --color-ink:     #1A1A1A;
    --color-blue:    #3B82F6;
    --color-orange:  #F97316;
    --radius-sm:  4px;
    --radius-md:  8px;
    --radius-lg:  12px;
    --radius-xl:  16px;
    --radius-full: 9999px;
    --font-body: 'Inter', system-ui, sans-serif;
    --font-mono: 'JetBrains Mono', monospace;
    --dur-fast: 100ms;
    --dur-base: 150ms;
    --ease: ease-out;
}

/* Global font */
html, body, [class*="css"], .stApp {
    font-family: var(--font-body) !important;
    color: var(--color-ink) !important;
    background-color: var(--color-bg) !important;
}

/* ── Layout ───────────────────────────────────────────── */
.main .block-container {
    padding-top: 2.5rem !important;
    padding-bottom: 3rem !important;
    max-width: 1200px !important;
}

/* ── Sidebar ──────────────────────────────────────────── */
[data-testid="stSidebar"] {
    background-color: var(--color-surface) !important;
    border-right: 0.5px solid var(--color-border) !important;
}
[data-testid="stSidebar"] * {
    font-family: var(--font-body) !important;
}

/* ── Typography ───────────────────────────────────────── */
h1, .stApp h1 {
    font-size: 36px !important;
    font-weight: 700 !important;
    letter-spacing: -0.02em !important;
    line-height: 1.15 !important;
    color: var(--color-fg) !important;
}
h2, .stApp h2 {
    font-size: 26px !important;
    font-weight: 600 !important;
    letter-spacing: -0.02em !important;
    color: var(--color-fg) !important;
}
h3, .stApp h3 {
    font-size: 20px !important;
    font-weight: 600 !important;
    letter-spacing: -0.01em !important;
    color: var(--color-fg) !important;
}
p, li, .stMarkdown p {
    font-size: 15px !important;
    color: var(--color-ink) !important;
    line-height: 1.6 !important;
}
[data-testid="stCaptionContainer"], .stCaptionContainer {
    font-size: 12px !important;
    color: var(--color-muted) !important;
}

/* ── Buttons — Primary ────────────────────────────────── */
.stButton > button[kind="primary"],
.stFormSubmitButton > button {
    background-color: var(--color-fg) !important;
    color: #FFFFFF !important;
    border: none !important;
    border-radius: var(--radius-md) !important;
    font-size: 13px !important;
    font-weight: 500 !important;
    padding: 8px 16px !important;
    font-family: var(--font-body) !important;
    transition: background-color var(--dur-base) var(--ease),
                transform var(--dur-fast) var(--ease) !important;
    box-shadow: none !important;
}
.stButton > button[kind="primary"]:hover,
.stFormSubmitButton > button:hover {
    background-color: var(--color-ink) !important;
    transform: translateY(-2px) !important;
    color: #FFFFFF !important;
}
.stButton > button[kind="primary"]:active {
    transform: scale(0.98) !important;
}
/* Force label + icon to inherit the button colour (overrides global p{}) */
.stButton > button[kind="primary"] *,
.stFormSubmitButton > button * {
    color: #FFFFFF !important;
}

/* ── Buttons — Secondary ──────────────────────────────── */
.stButton > button:not([kind="primary"]),
.stDownloadButton > button {
    background-color: transparent !important;
    color: var(--color-fg) !important;
    border: 0.5px solid var(--color-border) !important;
    border-radius: var(--radius-md) !important;
    font-size: 13px !important;
    font-weight: 500 !important;
    padding: 8px 16px !important;
    font-family: var(--font-body) !important;
    transition: background-color var(--dur-base) var(--ease),
                border-color var(--dur-base) var(--ease),
                transform var(--dur-fast) var(--ease) !important;
    box-shadow: none !important;
}
.stButton > button:not([kind="primary"]):hover,
.stDownloadButton > button:hover {
    background-color: var(--color-surface) !important;
    border-color: #C4C4C0 !important;
    transform: translateY(-2px) !important;
}
.stButton > button:not([kind="primary"]):active {
    transform: scale(0.98) !important;
}
/* Force label + icon to inherit the button colour (overrides global p{}) */
.stButton > button:not([kind="primary"]) *,
.stDownloadButton > button * {
    color: var(--color-fg) !important;
}
/* full-width override */
.stButton > button { width: 100%; }

/* ── Material Symbols icon helper (for raw-HTML contexts) ─ */
.msi {
    font-family: 'Material Symbols Outlined' !important;
    font-weight: normal;
    font-style: normal;
    line-height: 1;
    display: inline-flex;
    vertical-align: middle;
    font-size: 18px;
    -webkit-font-smoothing: antialiased;
    font-variation-settings: 'FILL' 0, 'wght' 400, 'GRAD' 0, 'opsz' 20;
    position: relative;
    top: -1px;
}

/* ── Inputs ───────────────────────────────────────────── */
.stTextInput > div > div > input,
.stTextArea > div > div > textarea,
.stNumberInput > div > div > input {
    border: 0.5px solid var(--color-border) !important;
    border-radius: var(--radius-md) !important;
    background-color: var(--color-bg) !important;
    font-size: 13px !important;
    font-family: var(--font-body) !important;
    color: var(--color-ink) !important;
    height: 40px !important;
    padding: 8px 12px !important;
    transition: border-color var(--dur-base) var(--ease) !important;
    box-shadow: none !important;
}
.stTextArea > div > div > textarea { height: auto !important; }
.stTextInput > div > div > input:focus,
.stTextArea > div > div > textarea:focus,
.stNumberInput > div > div > input:focus {
    border-color: var(--color-fg) !important;
    box-shadow: 0 0 0 2px rgba(0,0,0,0.08) !important;
    outline: none !important;
}

/* ── Selectbox ────────────────────────────────────────── */
.stSelectbox > div > div,
.stMultiSelect > div > div {
    border: 0.5px solid var(--color-border) !important;
    border-radius: var(--radius-md) !important;
    background-color: var(--color-bg) !important;
    font-size: 13px !important;
    font-family: var(--font-body) !important;
    min-height: 40px !important;
}

/* ── Expanders ────────────────────────────────────────── */
.stExpander {
    border: 0.5px solid var(--color-border) !important;
    border-radius: var(--radius-lg) !important;
    background-color: var(--color-bg) !important;
    overflow: hidden !important;
    margin-bottom: 8px !important;
    box-shadow: none !important;
}
.stExpander > details > summary {
    font-size: 14px !important;
    font-weight: 500 !important;
    color: var(--color-ink) !important;
    padding: 12px 16px !important;
    background-color: var(--color-bg) !important;
}
.stExpander > details > summary:hover {
    background-color: var(--color-surface) !important;
}

/* ── Containers with border ───────────────────────────── */
[data-testid="stVerticalBlockBorderWrapper"] {
    border: 0.5px solid var(--color-border) !important;
    border-radius: var(--radius-lg) !important;
    padding: 20px 24px !important;
    background-color: var(--color-bg) !important;
    box-shadow: none !important;
}

/* ── Metrics ──────────────────────────────────────────── */
[data-testid="stMetric"] {
    background-color: var(--color-surface) !important;
    border: 0.5px solid var(--color-border) !important;
    border-radius: var(--radius-lg) !important;
    padding: 16px 20px !important;
}
[data-testid="stMetricLabel"] > div {
    font-size: 11px !important;
    font-weight: 500 !important;
    text-transform: uppercase !important;
    letter-spacing: 0.06em !important;
    color: var(--color-muted) !important;
}
[data-testid="stMetricValue"] > div {
    font-size: 30px !important;
    font-weight: 700 !important;
    letter-spacing: -0.02em !important;
    color: var(--color-fg) !important;
    line-height: 1.15 !important;
}

/* ── Tabs ─────────────────────────────────────────────── */
.stTabs [data-baseweb="tab-list"] {
    background-color: transparent !important;
    border-bottom: 0.5px solid var(--color-border) !important;
    gap: 4px !important;
}
.stTabs [data-baseweb="tab"] {
    background-color: transparent !important;
    color: var(--color-muted) !important;
    font-size: 13px !important;
    font-weight: 500 !important;
    padding: 8px 14px !important;
    border-radius: 0 !important;
    border-bottom: 2px solid transparent !important;
    transition: color var(--dur-base) var(--ease) !important;
}
.stTabs [data-baseweb="tab"]:hover {
    color: var(--color-fg) !important;
    background-color: transparent !important;
}
.stTabs [aria-selected="true"] {
    color: var(--color-fg) !important;
    border-bottom: 2px solid var(--color-fg) !important;
    background-color: transparent !important;
    font-weight: 600 !important;
}
.stTabs [data-baseweb="tab-highlight"] {
    background-color: var(--color-fg) !important;
    height: 2px !important;
}
.stTabs [data-baseweb="tab-panel"] {
    padding-top: 24px !important;
}

/* ── Progress ─────────────────────────────────────────── */
[data-testid="stProgress"] > div {
    background-color: var(--color-border) !important;
    border-radius: var(--radius-full) !important;
    height: 4px !important;
}
[data-testid="stProgress"] > div > div {
    background-color: var(--color-fg) !important;
    border-radius: var(--radius-full) !important;
}

/* ── Alerts ───────────────────────────────────────────── */
[data-testid="stAlert"] {
    border-radius: var(--radius-md) !important;
    font-size: 13px !important;
    border-width: 0.5px !important;
    border-style: solid !important;
    box-shadow: none !important;
}

/* ── Dividers ─────────────────────────────────────────── */
hr {
    border: none !important;
    border-top: 0.5px solid var(--color-border) !important;
    margin: 24px 0 !important;
}

/* ── Code ─────────────────────────────────────────────── */
code, .stCode code {
    font-family: var(--font-mono) !important;
    font-size: 12px !important;
    background-color: var(--color-surface) !important;
    border-radius: var(--radius-sm) !important;
    padding: 2px 6px !important;
    color: var(--color-ink) !important;
    border: 0.5px solid var(--color-border) !important;
}
pre, .stCode pre {
    background-color: var(--color-surface) !important;
    border-radius: var(--radius-md) !important;
    padding: 12px 16px !important;
    border: 0.5px solid var(--color-border) !important;
}
pre code { border: none !important; padding: 0 !important; }

/* ── Dataframe / Table ────────────────────────────────── */
[data-testid="stDataFrame"],
[data-testid="stDataEditor"] {
    border: 0.5px solid var(--color-border) !important;
    border-radius: var(--radius-md) !important;
    overflow: hidden !important;
}

/* ── File uploader ────────────────────────────────────── */
[data-testid="stFileUploader"] > div {
    border: 0.5px dashed var(--color-border) !important;
    border-radius: var(--radius-md) !important;
    background-color: var(--color-surface) !important;
    transition: border-color var(--dur-base) var(--ease) !important;
}
[data-testid="stFileUploader"] > div:hover {
    border-color: #C4C4C0 !important;
}

/* ── Slider ───────────────────────────────────────────── */
[data-testid="stSlider"] [data-baseweb="slider"] [role="slider"] {
    background-color: var(--color-fg) !important;
    border-color: var(--color-fg) !important;
}
[data-testid="stSlider"] [data-baseweb="slider"] [data-testid="stThumbValue"] {
    color: var(--color-fg) !important;
}

/* ── Status widget ────────────────────────────────────── */
[data-testid="stStatusWidget"] {
    border: 0.5px solid var(--color-border) !important;
    border-radius: var(--radius-md) !important;
    font-size: 13px !important;
}

/* ── Spinner ──────────────────────────────────────────── */
[data-testid="stSpinner"] p {
    color: var(--color-muted) !important;
    font-size: 13px !important;
}

/* ── Status text ──────────────────────────────────────── */
.pass { color: #166534; font-weight: 600; }
.fail { color: #991B1B; font-weight: 600; }
.err  { color: #92400E; font-weight: 600; }

/* ── Ambiguity badge ──────────────────────────────────── */
.ambiguous-badge {
    background: #FFFBEB;
    color: #92400E;
    padding: 2px 8px;
    border-radius: var(--radius-full, 9999px);
    font-size: 11px;
    font-weight: 500;
    border: 0.5px solid #FDE68A;
}

/* ── Result card header ───────────────────────────────── */
.rc-header {
    display: flex; align-items: center; gap: 10px;
    padding: 4px 0 8px 0;
}
.rc-title { font-size: 14px; font-weight: 700; flex: 1; }
.rc-time  { font-size: 12px; color: var(--color-muted); white-space: nowrap; }

/* ── Inline badge chips ───────────────────────────────── */
.badge {
    display: inline-block;
    padding: 2px 10px;
    border-radius: 9999px;
    font-size: 11px;
    font-weight: 500;
    letter-spacing: 0.01em;
    margin-left: 4px;
    vertical-align: middle;
}
.badge-pass    { background: #F0FDF4; color: #166534; border: 0.5px solid #BBF7D0; }
.badge-fail    { background: #FEF2F2; color: #991B1B; border: 0.5px solid #FECACA; }
.badge-error   { background: #FFFBEB; color: #92400E; border: 0.5px solid #FDE68A; }
.badge-timeout { background: var(--color-surface); color: #374151; border: 0.5px solid var(--color-border); }
.badge-assert  { background: #EFF6FF; color: #1E40AF; border: 0.5px solid #BFDBFE; }
.badge-sel     { background: #F5F3FF; color: #4C1D95; border: 0.5px solid #DDD6FE; }
.badge-network { background: #FEF2F2; color: #991B1B; border: 0.5px solid #FECACA; }
.badge-auth    { background: #FFF7ED; color: #9A3412; border: 0.5px solid #FED7AA; }
.badge-flaky   { background: #FFFBEB; color: #92400E; border: 0.5px solid #FDE68A; }
.badge-retry   { background: var(--color-surface); color: #374151; border: 0.5px solid var(--color-border); }

/* ── KPI card strip ───────────────────────────────────── */
.kpi-label { font-size: 11px; color: var(--color-muted); text-transform: uppercase; letter-spacing: .06em; font-weight: 500; }
.kpi-value { font-size: 30px; font-weight: 700; line-height: 1.15; letter-spacing: -0.02em; color: var(--color-fg); }
.kpi-delta { font-size: 12px; color: var(--color-muted); }

/* ── Flaky indicator ──────────────────────────────────── */
.flaky-row { background: #FFFBEB; border-radius: var(--radius-sm); padding: 2px 0; }

/* ── Sidebar subheaders ───────────────────────────────── */
[data-testid="stSidebar"] .stMarkdown h3,
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] h3 {
    font-size: 11px !important;
    font-weight: 500 !important;
    text-transform: uppercase !important;
    letter-spacing: 0.06em !important;
    color: var(--color-muted) !important;
    margin-top: 20px !important;
    margin-bottom: 8px !important;
    border-bottom: 0.5px solid var(--color-border) !important;
    padding-bottom: 6px !important;
}
</style>
""", unsafe_allow_html=True)

# ── Session state ──────────────────────────────────────────────────────────
_init_state()

# ── UX constants ──────────────────────────────────────────────────────────
# Material Symbol names per status (used for both :material/…: markdown and HTML spans)
_STATUS_MICON = {"passed": "check_circle", "failed": "cancel", "error": "warning", "running": "hourglass_empty"}
_STATUS_COLOR = {"passed": "#166534", "failed": "#991B1B", "error": "#92400E", "running": "#1E40AF"}
_ERR_BADGE_CLASS = {
    "timeout":   "badge-timeout",
    "assertion": "badge-assert",
    "selector":  "badge-sel",
    "network":   "badge-network",
    "auth":      "badge-auth",
}

ASSERTION_ACTIONS = {
    "check_url", "check_text", "check_element",
    "check_attribute", "check_count",
}


# ── Icon helpers ───────────────────────────────────────────────────────────

def _icon(name: str, size: int = 18, color: str | None = None) -> str:
    """Material Symbol as an inline HTML span — for use inside unsafe_allow_html blocks."""
    style = f"font-size:{size}px"
    if color:
        style += f";color:{color}"
    return f'<span class="msi" style="{style}">{name}</span>'


def _mi(name: str) -> str:
    """Material Symbol token for Streamlit-native markdown/labels (headers, buttons, tabs…)."""
    return f":material/{name}:"


def _status_micon(status: str, size: int = 20) -> str:
    return _icon(_STATUS_MICON.get(status, "help"), size=size, color=_STATUS_COLOR.get(status, "#6B7280"))


# ── UI helpers ─────────────────────────────────────────────────────────────

def _render_error_message(ex) -> None:
    if not ex.error_message:
        return
    err_type = getattr(ex, "error_type", None)
    if err_type == "auth" or "Authentication failed" in ex.error_message:
        st.warning(
            f"**Auth failure:** {ex.error_message}\n\n"
            "Check the Login URL, username/password selectors, and credentials in the sidebar."
        )
    elif err_type == "assertion":
        st.error(f"**Assertion failed:** {ex.error_message}")
    elif err_type == "timeout":
        st.error(f"**Timeout:** {ex.error_message}")
    elif err_type == "selector":
        st.error(f"**Selector not found:** {ex.error_message}")
    elif err_type == "network":
        st.error(f"**Network error:** {ex.error_message}")
    else:
        st.error(ex.error_message)


def _status_badges_html(ex) -> str:
    parts = []
    css = _ERR_BADGE_CLASS.get(getattr(ex, "error_type", "") or "", "badge-error")
    if ex.error_type:
        parts.append(f'<span class="badge {css}">{ex.error_type}</span>')
    if getattr(ex, "attempts", 1) > 1:
        parts.append(f'<span class="badge badge-retry">{_icon("autorenew", 13)} {ex.attempts} tries</span>')
    vv = getattr(ex, "vision_verdict", None)
    if vv and not vv.get("passed", True):
        parts.append(f'<span class="badge badge-error">{_icon("visibility", 13)} vision fail</span>')
    return "".join(parts)


def _render_result_card(ex, tc_title: str = "") -> None:
    icon  = _status_micon(ex.status, size=20)
    color = _STATUS_COLOR.get(ex.status, "#6B7280")
    t     = f"{ex.execution_time:.2f}s" if ex.execution_time is not None else "—"
    label = tc_title or ex.test_case_id
    vlab  = f" · {ex.variation_label}" if getattr(ex, "variation_label", None) else ""
    badges = _status_badges_html(ex)

    with st.container(border=True):
        st.markdown(
            f'<div class="rc-header">'
            f'  {icon}'
            f'  <span class="rc-title" style="color:{color}">{ex.status.upper()}'
            f'    <span style="color:#1A1A1A;font-weight:500;font-size:0.88em"> — {label}{vlab}</span>'
            f'  </span>'
            f'  {badges}'
            f'  <span class="rc-time">{_icon("schedule", 14, "#6B7280")} {t}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )
        _render_error_message(ex)
        vv = getattr(ex, "vision_verdict", None)
        if vv:
            v_icon = _mi("check_circle") if vv.get("passed") else _mi("cancel")
            conf   = vv.get("confidence", 0)
            st.caption(f"{_mi('visibility')} Vision: {v_icon} confidence {conf:.0%} — {vv.get('explanation','')}")
        if ex.screenshots:
            visible = [s for s in ex.screenshots if os.path.exists(s)]
            if visible:
                cols = st.columns(min(3, len(visible)))
                for i, s in enumerate(visible):
                    cols[i % 3].image(s, use_container_width=True)


def _session_error_breakdown(executions) -> dict:
    counts: dict = {}
    for ex in executions:
        if ex.status != "passed" and ex.error_type:
            counts[ex.error_type] = counts.get(ex.error_type, 0) + 1
    return counts


# ── Cached resources ───────────────────────────────────────────────────────

@st.cache_resource(show_spinner=False)
def _get_storage():
    if config.AZURE_STORAGE_CONNECTION_STRING:
        return AzureStorageManager()
    return LocalStorageManager()


@st.cache_resource(show_spinner=False)
def _get_db():
    if not DatabaseManager.is_configured():
        return None
    try:
        return DatabaseManager()
    except Exception:
        return None


@st.cache_resource(show_spinner=False)
def _get_llm():
    return LLMProcessor()


@st.cache_resource(show_spinner=False)
def _get_workflow() -> WorkflowService:
    return WorkflowService(_get_llm(), _get_storage())


@st.cache_resource(show_spinner=False)
def _get_report_service() -> ReportService:
    return ReportService(_get_llm(), _get_storage(), _get_db())


# ── Init components ────────────────────────────────────────────────────────
db = None
try:
    config.validate()
    llm      = _get_llm()
    storage  = _get_storage()
    db       = _get_db()
    workflow = _get_workflow()
    report_svc = _get_report_service()
    if db is None and DatabaseManager.is_configured():
        st.warning("DB connection failed (history disabled).", icon=":material/warning:")
    st.session_state.ready = True
except Exception as e:
    st.error(f"Initialisation error: {e}")
    st.session_state.ready = False

# ── Sidebar ────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header(f"{_mi('settings')} Configuration")

    # ── Application URL ───────────────────────────────────────────────────
    st.subheader("Application")
    raw_url = st.text_input("App URL", placeholder="https://example.com")
    base_url = ""
    if raw_url:
        base_url = raw_url.strip()
        if not base_url.startswith(("http://", "https://")):
            base_url = "https://" + base_url

    # REQ 3.1 / 3.4 — URL pre-flight connectivity check
    if base_url:
        col_dom, col_ping = st.columns(2)
        with col_ping:
            if st.button(f"{_mi('language')} Test URL", help="Send a quick HEAD request to verify the URL is reachable before running tests"):
                with st.spinner("Checking…"):
                    try:
                        import requests as _requests
                        resp = _requests.head(base_url, timeout=8, allow_redirects=True)
                        if resp.status_code < 400:
                            st.success(f"Reachable ({resp.status_code})", icon=":material/check_circle:")
                        else:
                            st.warning(f"HTTP {resp.status_code} — site may require auth or redirect", icon=":material/warning:")
                    except Exception as ping_err:
                        st.error(f"Unreachable: {ping_err}", icon=":material/error:")
        with col_dom:
            if st.button(f"{_mi('search')} Inspect DOM", help="Navigate to the app and extract real CSS selectors"):
                with st.spinner("Inspecting live app DOM…"):
                    try:
                        snapshot = inspect_dom(
                            base_url=base_url,
                            browser_type="chromium",
                            headless=True,
                            timeout=30000,
                            credentials=st.session_state.get("_sidebar_credentials"),
                        )
                        state.dom_snapshot = snapshot
                        if snapshot.get("error"):
                            st.warning(f"Inspection warning: {snapshot['error']}")
                        else:
                            input_count = len(snapshot.get("inputs", []))
                            btn_count   = len(snapshot.get("buttons", []))
                            st.success(
                                f"Inspected: {snapshot.get('title', base_url)} "
                                f"({input_count} inputs, {btn_count} buttons)"
                            )
                    except Exception as e:
                        st.error(f"DOM inspection failed: {e}")

        snap = state.dom_snapshot
        if snap and not snap.get("error"):
            with st.expander(f"{_mi('content_paste')} DOM Snapshot", expanded=False):
                st.caption(f"URL: {snap.get('url', '')}")
                st.caption(f"Title: {snap.get('title', '')}")
                inputs = snap.get("inputs", [])
                if inputs:
                    st.markdown("**Inputs:**")
                    for inp in inputs[:8]:
                        label = f" [{inp['label']}]" if inp.get("label") else ""
                        st.code(f"{inp['selector']}  ({inp.get('type','text')}){label}", language=None)
                btns = snap.get("buttons", [])
                if btns:
                    st.markdown("**Buttons:**")
                    for btn in btns[:6]:
                        st.code(f"{btn['selector']}  \"{btn.get('text','')[:40]}\"", language=None)

    # ── Browser settings ──────────────────────────────────────────────────
    st.subheader("Browser")
    browser  = st.selectbox("Browser", ["chromium", "firefox", "webkit"])
    headless = st.checkbox("Headless", value=True)
    timeout  = st.number_input("Timeout (ms)", value=30000, min_value=5000, max_value=120000, step=5000)

    st.subheader("Reliability (Phase 1)")
    max_retries = st.slider("Retry on timeout", min_value=0, max_value=3, value=0,
                            help="Retry the entire test if a step times out.")
    use_vision  = st.checkbox("Vision verification", value=False,
                              help="Use Claude vision to verify the final screenshot against expected results.")
    per_step_ss = st.checkbox(
        "Per-step screenshots",
        value=False,
        help="Capture a screenshot after EVERY step. Useful for debugging failures — slower.",
    )

    st.subheader("Coverage (Phase 2)")
    shared_session      = st.checkbox("Shared browser session", value=False,
                                      help="Authenticate once and share the browser context across all tests.")
    generate_variations = st.checkbox("Generate test variations", value=False,
                                      help="Ask Claude to generate 2-3 parameterized variations per test case.")

    # ── Authentication ────────────────────────────────────────────────────
    st.subheader("Authentication")
    auth_type = st.selectbox(
        "Auth method",
        ["form", "cookie", "token"],
        format_func=lambda x: {
            "form":   "Form (username + password)",
            "cookie": "Cookie injection",
            "token":  "Bearer token",
        }[x],
    )

    use_auth = st.checkbox("Enable Auth")
    credentials = None
    u_sel = "#username"
    p_sel = "#password"
    s_sel = ""

    if use_auth:
        if auth_type == "form":
            login_url = st.text_input("Login URL", placeholder="https://example.com/login")
            col1, col2 = st.columns(2)
            with col1:
                u_sel    = st.text_input("Username selector", value="#username")
                username = st.text_input("Username")
            with col2:
                p_sel    = st.text_input("Password selector", value="#password")
                password = st.text_input("Password", type="password")
            s_sel = st.text_input("Submit selector (optional)", placeholder="button[type='submit']")
            if username and password:
                credentials = {
                    "login_url":         login_url or "",
                    "username_selector": u_sel,
                    "password_selector": p_sel,
                    "submit_selector":   s_sel,
                    "username":          username,
                    "password":          password,
                }
        elif auth_type == "cookie":
            st.caption("Paste a JSON array of cookie objects.")
            cookie_json = st.text_area("Cookies (JSON)", height=100)
            if cookie_json.strip():
                credentials = {"cookies": cookie_json.strip()}
        elif auth_type == "token":
            token_val = st.text_input("Bearer token", type="password")
            if token_val.strip():
                credentials = {"token": token_val.strip()}

    st.session_state["_sidebar_credentials"] = credentials

    playwright_config = None
    if base_url:
        playwright_config = PlaywrightConfig(
            base_url=base_url,
            browser=browser,
            headless=headless,
            timeout=timeout,
            credentials=credentials,
            max_retries=max_retries,
            auth_type=auth_type,
            shared_session=shared_session,
            per_step_screenshots=per_step_ss,
        )

    # ── Rate limit indicator ───────────────────────────────────────────────
    st.subheader(f"{_mi('bar_chart')} Session Usage")
    used = workflow.api_call_count if st.session_state.get("ready") else 0
    cap  = config.MAX_API_CALLS_PER_SESSION
    pct  = min(used / cap, 1.0) if cap else 0
    st.progress(pct, text=f"API calls: {used} / {cap}")
    if used >= cap:
        st.error("Session API call limit reached. Refresh the page to reset.", icon=":material/block:")
    elif used >= cap * 0.8:
        st.warning(f"Approaching API call limit ({used}/{cap}).", icon=":material/warning:")


# ── Main ───────────────────────────────────────────────────────────────────
st.markdown(
    '<p style="font-size:11px;font-weight:500;text-transform:uppercase;letter-spacing:.06em;'
    'color:#6B7280;margin-bottom:4px">QA Infrastructure</p>',
    unsafe_allow_html=True,
)
st.title("QA Test Agent")
st.caption("Automated browser testing powered by Claude AI and Playwright")

if not st.session_state.get("ready"):
    st.stop()

# ── Top-level tabs ─────────────────────────────────────────────────────────
main_tab, history_tab = st.tabs([f"{_mi('science')} Test Agent", f"{_mi('folder')} Run History"])

# ═══════════════════════════════════════════════════════════════════════════
# TAB 1 — Test Agent (main workflow)
# ═══════════════════════════════════════════════════════════════════════════
with main_tab:

    # ── Step 1: Input requirements ─────────────────────────────────────────
    st.header(f"{_mi('counter_1')} Requirements")
    tab1, tab2, tab3 = st.tabs([f"{_mi('upload_file')} Upload", f"{_mi('edit_note')} Paste", f"{_mi('bolt')} Sample"])

    with tab1:
        files = st.file_uploader(
            "Upload docs (PDF, DOCX, TXT, MD) — multiple allowed",
            type=["pdf", "docx", "txt", "md"],
            accept_multiple_files=True,
        )
        if files and st.button("Extract Requirements from Files", type="primary"):
            with st.spinner("Extracting…"):
                all_content = []
                for f in files:
                    try:
                        all_content.append(workflow.extract_file_content(f))
                    except Exception as e:
                        st.error(f"{f.name}: {e}")

                if all_content:
                    merged_content = "\n\n".join(all_content)
                    try:
                        state.requirements = workflow.analyze_requirements(merged_content)
                        st.success(f"Extracted {len(state.requirements)} requirements from {len(files)} file(s)")
                    except Exception as e:
                        st.error(str(e))

    with tab2:
        text = st.text_area("Paste requirements or user stories:", height=250)
        if st.button("Analyze", type="primary"):
            if text.strip():
                with st.spinner("Analyzing…"):
                    try:
                        state.requirements = workflow.analyze_requirements(text)
                        st.success(f"Extracted {len(state.requirements)} requirements")
                    except Exception as e:
                        st.error(str(e))

    with tab3:
        if st.button("Load sample"):
            sample = """
REQ-001: User Login
Users log in with email and password.
Criteria: Valid credentials grant access. Invalid credentials show error.

REQ-002: Redirect After Login
After login, redirect to dashboard.
Criteria: Dashboard loads after successful login.
"""
            with st.spinner("Processing…"):
                try:
                    state.requirements = workflow.analyze_requirements(sample)
                    st.success(f"Loaded {len(state.requirements)} requirements")
                except Exception as e:
                    st.error(str(e))

    # ── REQ 1.4 — Ambiguity flagging ──────────────────────────────────────
    if state.requirements:
        col_req, col_amb = st.columns([3, 1])
        with col_amb:
            if st.button(f"{_mi('search')} Check Ambiguity", help="Score each requirement for clarity and testability"):
                with st.spinner("Analysing requirement clarity…"):
                    try:
                        state.ambiguity_scores = workflow.flag_ambiguous_requirements(state.requirements)
                    except Exception as e:
                        st.error(f"Ambiguity check failed: {e}")

        amb_lookup = {s["requirement_id"]: s for s in state.ambiguity_scores}

        for req in state.requirements:
            amb = amb_lookup.get(req.id, {})
            score = amb.get("clarity_score", 1.0)
            is_ambiguous = score < 0.7

            badge = (
                f' <span class="ambiguous-badge">{_icon("warning", 12)} Ambiguous ({score:.0%})</span>'
                if is_ambiguous else ""
            )
            label = f"**{req.title}** ({req.id}){badge}"

            with st.expander(label, expanded=is_ambiguous):
                st.write(req.description)
                for c in req.acceptance_criteria:
                    st.markdown(f"- {c}")

                if is_ambiguous:
                    if amb.get("issues"):
                        st.warning("**Issues found:** " + " · ".join(amb["issues"]))
                    if amb.get("suggestion"):
                        st.info(f"**Suggestion:** {amb['suggestion']}", icon=":material/lightbulb:")
                    clarity_key = f"clarify_{req.id}"
                    existing    = state.clarifications.get(req.id, "")
                    new_text    = st.text_area(
                        "Add clarification (appended to requirement before generation):",
                        value=existing,
                        key=clarity_key,
                        height=80,
                    )
                    if new_text != existing:
                        state.clarifications[req.id] = new_text

    # ── REQ 11 — Optional Design Asset Upload ─────────────────────────────
    if state.requirements:
        st.markdown("---")
        with st.expander(f"{_mi('palette')} Design Assets (Optional — REQ 11)", expanded=False):
            st.caption(
                "Upload a screenshot or mockup of your app's UI. Claude will analyse it against "
                "your requirements and inject visual validation steps into generated tests."
            )
            design_file = st.file_uploader(
                "Upload design image (PNG, JPG, WEBP)", type=["png", "jpg", "jpeg", "webp"], key="design_upload"
            )
            figma_url = st.text_input("Or paste a Figma/image URL", placeholder="https://…", key="figma_url")

            if st.button(f"{_mi('search')} Analyse Design", key="analyse_design"):
                image_b64  = None
                media_type = "image/png"
                with st.spinner("Analysing design asset…"):
                    try:
                        if design_file:
                            import base64
                            raw_bytes  = design_file.read()
                            image_b64  = base64.standard_b64encode(raw_bytes).decode()
                            ext        = design_file.name.rsplit(".", 1)[-1].lower()
                            media_type = f"image/{'jpeg' if ext in ('jpg','jpeg') else ext}"
                        elif figma_url.strip():
                            image_b64, media_type = workflow.fetch_image_from_url(figma_url.strip())

                        if image_b64:
                            result = workflow.analyze_design(image_b64, state.requirements, media_type=media_type)
                            state.design_context       = result.get("design_context", "")
                            state.design_discrepancies = result.get("discrepancies", [])

                            if result.get("ui_elements"):
                                st.success(f"Found {len(result['ui_elements'])} UI elements in the design.")
                            if result.get("discrepancies"):
                                st.warning("**Discrepancies vs requirements:**")
                                for d in result["discrepancies"]:
                                    st.markdown(f"- {d}")
                            if result.get("visual_checks"):
                                st.info("**Visual checks to be injected into tests:**")
                                for vc in result["visual_checks"]:
                                    st.markdown(f"- `{vc}`")
                        else:
                            st.warning("Please upload an image or provide a URL first.")
                    except Exception as e:
                        st.error(f"Design analysis failed: {e}")

            if state.design_context:
                st.success("Design context ready — will be injected into next test generation.", icon=":material/check_circle:")
                if st.button("Clear design context", key="clear_design"):
                    state.design_context       = None
                    state.design_discrepancies = []

    # ── REQ 10.3 — External test data upload ──────────────────────────────
    if state.requirements:
        with st.expander(f"{_mi('folder_open')} External Test Data (Optional — REQ 10.3)", expanded=False):
            st.caption("Upload a CSV or JSON file with test data rows. Each row will be used as a variation.")
            td_file = st.file_uploader("Upload CSV or JSON", type=["csv", "json"], key="testdata_upload")
            if td_file:
                try:
                    if td_file.name.endswith(".csv"):
                        df = pd.read_csv(td_file)
                        state.external_test_data = df.to_dict(orient="records")
                    else:
                        raw = json.loads(td_file.read().decode())
                        if isinstance(raw, list):
                            state.external_test_data = raw
                        elif isinstance(raw, dict):
                            state.external_test_data = [raw]
                        else:
                            st.error("JSON must be an array of objects or a single object.")
                    if state.external_test_data:
                        st.success(f"Loaded {len(state.external_test_data)} data row(s).")
                        st.dataframe(pd.DataFrame(state.external_test_data).head(5), hide_index=True)
                except Exception as e:
                    st.error(f"Failed to parse test data: {e}")
            if state.external_test_data:
                if st.button("Clear test data", key="clear_td"):
                    state.external_test_data = None

    # ── REQ 10.4 — Custom assertions ─────────────────────────────────────
    if state.requirements:
        with st.expander(f"{_mi('edit')} Custom Assertion Rules (Optional — REQ 10.4)", expanded=False):
            st.caption(
                "Add custom text or attribute assertions. These are injected as `check_text` / "
                "`check_attribute` steps into every generated test case."
            )
            new_rule_text = st.text_input(
                "Assertion rule", placeholder='e.g. "Welcome" or "aria-label=Submit"', key="custom_assert_input"
            )
            if st.button(f"{_mi('add')} Add Rule", key="add_custom_assert"):
                rule = new_rule_text.strip()
                if rule and rule not in state.custom_assertions:
                    state.custom_assertions.append(rule)

            if state.custom_assertions:
                st.markdown("**Active rules:**")
                to_remove = []
                for i, rule in enumerate(state.custom_assertions):
                    c1, c2 = st.columns([5, 1])
                    c1.code(rule, language=None)
                    if c2.button(_mi("close"), key=f"remove_rule_{i}"):
                        to_remove.append(rule)
                for r in to_remove:
                    state.custom_assertions.remove(r)

    # ── Step 2: Generate test cases ────────────────────────────────────────
    if state.requirements:
        st.header(f"{_mi('counter_2')} Generate Test Cases")
        max_tc = st.slider("Max test cases", 1, 10, 5)

        snap = state.dom_snapshot
        if snap and not snap.get("error"):
            st.info(
                f"DOM snapshot available ({len(snap.get('inputs', []))} inputs, "
                f"{len(snap.get('buttons', []))} buttons) — real selectors will be injected."
            )
        elif base_url:
            st.caption(f"{_mi('lightbulb')} Tip: Click **Inspect DOM** in the sidebar to inject real selectors and reduce selector failures.")

        if state.design_context:
            st.info("Design context will be injected into test generation.", icon=":material/palette:")
        if state.custom_assertions:
            st.info(f"{len(state.custom_assertions)} custom assertion rule(s) will be appended to every test.", icon=":material/edit:")
        if state.external_test_data:
            st.info(f"{len(state.external_test_data)} external data row(s) loaded — will be used as variations.", icon=":material/folder_open:")

        _limit_hit = workflow.rate_limit_exceeded()
        if _limit_hit:
            st.error("Session API call limit reached. Refresh the page to generate more test cases.", icon=":material/block:")
        if st.button(f"{_mi('science')} Generate Test Cases", type="primary", width="stretch", disabled=_limit_hit):
            if not state.generating:
                state.generating = True
                with st.spinner("Generating…"):
                    try:
                        reqs_for_gen = workflow.apply_clarifications(
                            state.requirements, state.clarifications
                        )
                        tcs = workflow.generate_test_cases(
                            reqs_for_gen,
                            username_selector=u_sel,
                            password_selector=p_sel,
                            submit_selector=s_sel,
                            max_cases=max_tc,
                            dom_snapshot=state.dom_snapshot,
                            generate_variations=generate_variations,
                            design_context=state.design_context,
                        )

                        if state.external_test_data:
                            workflow.inject_external_data(tcs, state.external_test_data)
                        if state.custom_assertions:
                            workflow.inject_custom_assertions(tcs, state.custom_assertions)

                        state.test_cases    = tcs
                        st.session_state.selected_tests = [tc.id for tc in tcs]
                        variation_total = sum(len(tc.variations) for tc in tcs)
                        if variation_total:
                            st.success(
                                f"Generated {len(tcs)} test cases "
                                f"({variation_total} variations across all tests)"
                            )
                        else:
                            st.success(f"Generated {len(tcs)} test cases")
                    except Exception as e:
                        st.error(str(e))
                    finally:
                        state.generating = False

    # ── Step 3: Test Management ────────────────────────────────────────────
    if state.test_cases:
        st.header(f"{_mi('counter_3')} Select & Manage Tests")

        _ALL_ACTIONS = sorted([
            "goto", "fill", "click", "check", "press",
            "wait_for_selector", "wait_for_load_state", "wait_for_timeout",
            "scroll_to", "hover", "select", "click_text",
            "check_url", "check_text", "check_element",
            "check_attribute", "check_count",
            "dismiss_modal", "iframe_switch", "iframe_exit",
            "wait_for_stable", "select_custom", "upload_file", "drag_drop",
        ])

        _EDITOR_CFG = {
            "#":       st.column_config.NumberColumn(disabled=True, width="small"),
            "Type":    st.column_config.TextColumn(disabled=True, width="small"),
            "Action":  st.column_config.SelectboxColumn(options=_ALL_ACTIONS, width="medium"),
            "Selector": st.column_config.TextColumn(width="medium"),
            "Value":   st.column_config.TextColumn(width="medium"),
            "Force":   st.column_config.CheckboxColumn(width="small"),
        }

        def _steps_to_df(tc):
            return pd.DataFrame([
                {
                    "#": i + 1,
                    "Type": "Assert" if s.action in ASSERTION_ACTIONS else "Action",
                    "Action": s.action,
                    "Selector": s.selector or "",
                    "Value": s.value or "",
                    "Force": s.force,
                }
                for i, s in enumerate(tc.steps)
            ])

        def _df_to_steps(df):
            steps = []
            for _, row in df.iterrows():
                action = str(row.get("Action", "")).strip()
                if not action:
                    continue
                steps.append(_TS(
                    action=action,
                    selector=str(row.get("Selector", "")).strip() or None,
                    value=str(row.get("Value", "")).strip() or None,
                    force=bool(row.get("Force", False)),
                ))
            return steps

        # ── Toolbar ──────────────────────────────────────────────────────────
        all_suites = sorted({tc.suite or "Unsorted" for tc in state.test_cases})
        suite_opts = ["All"] + all_suites

        tb1, tb2, tb3, tb4 = st.columns([2, 3, 2, 2])
        with tb1:
            suite_filter = st.selectbox(f"{_mi('folder')} Suite", suite_opts, key="suite_filter")
        with tb2:
            bulk_action = st.selectbox(
                "Bulk action",
                ["— select —", "Approve selected", "Regenerate selected",
                 "Add step to selected", "Delete selected"],
                key="bulk_action",
            )
        with tb3:
            bulk_apply = st.button(f"{_mi('play_arrow')} Apply", key="bulk_apply_btn", use_container_width=True)
        with tb4:
            if st.button(f"{_mi('add')} New test", key="new_test_btn", use_container_width=True):
                state.show_create_form = not state.show_create_form

        # ── Create-test form ─────────────────────────────────────────────────
        if state.show_create_form:
            with st.container(border=True):
                st.subheader(f"{_mi('edit')} Create test manually")
                cf1, cf2 = st.columns(2)
                with cf1:
                    new_title  = st.text_input("Title *", key="new_tc_title",
                                               placeholder="e.g. Verify login button")
                    new_suite  = st.text_input("Suite/folder", key="new_tc_suite",
                                               placeholder="e.g. Authentication")
                with cf2:
                    req_labels = (
                        [f"{r.id} — {r.title}" for r in state.requirements]
                        if state.requirements else ["(none)"]
                    )
                    new_req_sel  = st.selectbox("Requirement", req_labels, key="new_tc_req")
                    new_expected = st.text_area("Expected results (one per line)",
                                                key="new_tc_expected", height=80)

                st.markdown("**Steps** *(add rows below)*")
                _empty_df = pd.DataFrame(
                    columns=["#", "Type", "Action", "Selector", "Value", "Force"]
                )
                new_steps_df = st.data_editor(
                    _empty_df, key="new_tc_steps_editor",
                    num_rows="dynamic", hide_index=True,
                    column_config=_EDITOR_CFG,
                )

                cfa, cfb = st.columns(2)
                with cfa:
                    if st.button(f"{_mi('save')} Save test", key="save_new_tc", type="primary"):
                        if not new_title.strip():
                            st.error("Title is required.")
                        else:
                            req_id = ""
                            if state.requirements and "(none)" not in req_labels:
                                req_id = new_req_sel.split(" — ")[0]
                            steps  = _df_to_steps(new_steps_df) if not new_steps_df.empty else []
                            expect = [ln.strip() for ln in new_expected.strip().splitlines() if ln.strip()]
                            tc_id  = f"TC-{uuid.uuid4().hex[:8].upper()}"
                            new_tc = _TC(
                                id=tc_id,
                                requirement_id=req_id,
                                title=new_title.strip(),
                                steps=steps,
                                test_data={},
                                expected_results=expect,
                                suite=new_suite.strip() or None,
                                approved=False,
                            )
                            state.test_cases.append(new_tc)
                            state.show_create_form = False
                            st.success(f"Created {tc_id} — {new_title.strip()}")
                            st.rerun()
                with cfb:
                    if st.button(f"{_mi('close')} Cancel", key="cancel_new_tc"):
                        state.show_create_form = False
                        st.rerun()

        # ── Bulk add-step form ────────────────────────────────────────────────
        if state.show_bulk_step_form:
            with st.container(border=True):
                st.subheader(f"{_mi('add')} Add step to selected tests")
                bs1, bs2, bs3 = st.columns([2, 2, 2])
                with bs1:
                    bs_action = st.selectbox("Action", _ALL_ACTIONS, key="bulk_step_action")
                with bs2:
                    bs_sel = st.text_input("Selector", key="bulk_step_sel")
                with bs3:
                    bs_val = st.text_input("Value", key="bulk_step_val")
                bpos_col, bconf_col, bcanc_col = st.columns(3)
                bs_pos = bpos_col.radio("Insert at", ["End", "Beginning"],
                                        key="bulk_step_pos", horizontal=True)
                if bconf_col.button("Apply to selected", key="bulk_step_confirm"):
                    targets = {tc.id for tc in state.test_cases
                               if st.session_state.get(f"chk_{tc.id}", True)}
                    new_step = _TS(action=bs_action,
                                   selector=bs_sel.strip() or None,
                                   value=bs_val.strip() or None)
                    for tc in state.test_cases:
                        if tc.id in targets:
                            if bs_pos == "Beginning":
                                tc.steps.insert(0, new_step)
                            else:
                                tc.steps.append(new_step)
                            tc.approved = False
                    state.show_bulk_step_form = False
                    st.success(f"Step added to {len(targets)} test(s). Approval reset.")
                    st.rerun()
                if bcanc_col.button(f"{_mi('close')} Cancel", key="bulk_step_cancel"):
                    state.show_bulk_step_form = False
                    st.rerun()

        # ── Filter + select-all ───────────────────────────────────────────────
        filtered_tcs = [
            tc for tc in state.test_cases
            if suite_filter == "All" or (tc.suite or "Unsorted") == suite_filter
        ]

        if filtered_tcs:
            sa_col, _ = st.columns([1, 11])
            if sa_col.checkbox("All", key="select_all_chk"):
                for tc in filtered_tcs:
                    st.session_state[f"chk_{tc.id}"] = True

        # ── Handle bulk-action apply ──────────────────────────────────────────
        if bulk_apply and bulk_action != "— select —":
            selected_now = {tc.id for tc in filtered_tcs
                            if st.session_state.get(f"chk_{tc.id}", True)}
            if not selected_now:
                st.warning("No tests selected.")
            elif "Approve" in bulk_action:
                for tc in state.test_cases:
                    if tc.id in selected_now:
                        tc.approved = True
                st.success(f"Approved {len(selected_now)} test(s).")
                st.rerun()
            elif "Delete" in bulk_action:
                state.test_cases = [tc for tc in state.test_cases if tc.id not in selected_now]
                st.success(f"Deleted {len(selected_now)} test(s).")
                st.rerun()
            elif "Regenerate" in bulk_action:
                with st.status(f"Regenerating {len(selected_now)} test(s)…",
                               expanded=True) as _rstat:
                    for tc in list(state.test_cases):
                        if tc.id not in selected_now:
                            continue
                        st.write(f"{_mi('hourglass_empty')} {tc.title}…")
                        new_tc, err = workflow.regenerate_one(
                            tc, state.requirements,
                            username_selector=u_sel, password_selector=p_sel,
                            submit_selector=s_sel, dom_snapshot=state.dom_snapshot,
                            design_context=state.design_context,
                        )
                        if new_tc:
                            idx = next(i for i, t in enumerate(state.test_cases) if t.id == tc.id)
                            state.test_cases[idx] = new_tc
                            st.write(f"{_mi('check_circle')} {tc.title}")
                        else:
                            st.write(f"{_mi('cancel')} {tc.title}: {err}")
                    _rstat.update(label="Bulk regeneration complete", state="complete", expanded=False)
                st.rerun()
            elif "Add step" in bulk_action:
                state.show_bulk_step_form = True
                st.rerun()

        # ── Render tests grouped by suite ─────────────────────────────────────
        suite_groups: dict = defaultdict(list)
        for tc in filtered_tcs:
            suite_groups[tc.suite or "Unsorted"].append(tc)

        sorted_suite_keys = sorted(k for k in suite_groups if k != "Unsorted")
        if "Unsorted" in suite_groups:
            sorted_suite_keys.append("Unsorted")

        selected_ids = []
        for s_name in sorted_suite_keys:
            s_tcs = suite_groups[s_name]
            n_approved = sum(1 for t in s_tcs if t.approved)

            if len(sorted_suite_keys) > 1 or s_name != "Unsorted":
                st.markdown(
                    f'{_icon("folder", 15)} <b>{s_name}</b> &nbsp;&nbsp;'
                    f'<span class="badge badge-pass">{n_approved} approved</span> '
                    f'<span class="badge badge-retry">{len(s_tcs) - n_approved} pending</span>',
                    unsafe_allow_html=True,
                )

            for tc in s_tcs:
                (c_chk, c_status, c_title,
                 c_suite, c_approve, c_dup, c_del) = st.columns([0.5, 1, 4, 1.5, 1.2, 0.8, 0.8])

                with c_chk:
                    checked = st.checkbox(
                        "", value=st.session_state.get(f"chk_{tc.id}", True),
                        key=f"chk_{tc.id}", label_visibility="collapsed",
                    )
                if checked:
                    selected_ids.append(tc.id)

                with c_status:
                    if tc.approved:
                        st.markdown(f'<span class="badge badge-pass">{_icon("check_circle", 13)} approved</span>', unsafe_allow_html=True)
                    else:
                        st.markdown(f'<span class="badge badge-error">{_icon("hourglass_empty", 13)} pending</span>', unsafe_allow_html=True)

                with c_title:
                    st.markdown(f"**{tc.title}** `{tc.id}`")

                with c_suite:
                    new_suite_val = st.text_input(
                        "Suite", value=tc.suite or "",
                        key=f"suite_input_{tc.id}",
                        label_visibility="collapsed",
                        placeholder="Suite…",
                    )
                    if new_suite_val.strip() != (tc.suite or ""):
                        tc.suite = new_suite_val.strip() or None

                with c_approve:
                    if not tc.approved:
                        if st.button(f"{_mi('check_circle')} Approve", key=f"approve_{tc.id}"):
                            tc.approved = True
                            st.rerun()
                    else:
                        if st.button(f"{_mi('undo')} Revoke", key=f"revoke_{tc.id}"):
                            tc.approved = False
                            st.rerun()

                with c_dup:
                    if st.button(_mi("content_copy"), key=f"dup_{tc.id}", help="Duplicate"):
                        dup = copy.deepcopy(tc)
                        dup.id = f"TC-{uuid.uuid4().hex[:8].upper()}"
                        dup.title = f"{tc.title} (copy)"
                        dup.approved = False
                        idx = next(i for i, t in enumerate(state.test_cases) if t.id == tc.id)
                        state.test_cases.insert(idx + 1, dup)
                        st.rerun()

                with c_del:
                    if st.button(_mi("delete"), key=f"del_{tc.id}", help="Delete"):
                        state.test_cases = [t for t in state.test_cases if t.id != tc.id]
                        if tc.id in selected_ids:
                            selected_ids.remove(tc.id)
                        st.rerun()

                with st.expander(f"{_mi('edit')} {tc.title}", expanded=False):
                    edited = st.data_editor(
                        _steps_to_df(tc),
                        key=f"editor_{tc.id}",
                        num_rows="dynamic",
                        hide_index=True,
                        column_config=_EDITOR_CFG,
                    )

                    ep1, ep2 = st.columns(2)
                    with ep1:
                        if st.button(f"{_mi('save')} Apply edits", key=f"apply_{tc.id}"):
                            new_steps = _df_to_steps(edited)
                            if new_steps:
                                tc.steps    = new_steps
                                tc.approved = False
                                st.success(f"{len(new_steps)} step(s) saved — approval reset.")
                            else:
                                st.warning("No valid steps.")
                    with ep2:
                        if st.button(f"{_mi('refresh')} Regenerate", key=f"regen_{tc.id}"):
                            with st.spinner("Regenerating…"):
                                new_tc, err = workflow.regenerate_one(
                                    tc, state.requirements,
                                    username_selector=u_sel, password_selector=p_sel,
                                    submit_selector=s_sel, dom_snapshot=state.dom_snapshot,
                                    design_context=state.design_context,
                                )
                            if new_tc:
                                idx = next(
                                    i for i, t in enumerate(state.test_cases) if t.id == tc.id
                                )
                                state.test_cases[idx] = new_tc
                                st.success("Regenerated — approval reset.")
                                st.rerun()
                            else:
                                st.error(err)

                    st.markdown("**Test data:**")
                    st.json(tc.test_data)

                    if tc.variations:
                        st.markdown(f"**Variations ({len(tc.variations)}):**")
                        for v in tc.variations:
                            st.markdown(
                                f"- **{v.get('label','?')}**: `{v.get('data',{})}` → "
                                f"{', '.join(v.get('expected_results',[]))}"
                            )

                    st.markdown("**Display script:**")
                    st.code(tc.playwright_script, language="python")

            if s_name != sorted_suite_keys[-1]:
                st.divider()

        # ── Action bar ────────────────────────────────────────────────────────
        st.divider()

        pending_sel  = [tc for tc in state.test_cases if tc.id in selected_ids and not tc.approved]
        approved_sel = [tc for tc in state.test_cases if tc.id in selected_ids and tc.approved]

        if pending_sel:
            pw_col, pa_col = st.columns([3, 1])
            pw_col.warning(
                f"**{len(pending_sel)} selected test(s) not yet approved** — "
                "only approved tests will be executed.",
                icon=":material/warning:",
            )
            with pa_col:
                if st.button(f"{_mi('check_circle')} Approve all selected", key="approve_all_sel", use_container_width=True):
                    for tc in state.test_cases:
                        if tc.id in selected_ids:
                            tc.approved = True
                    st.rerun()

        if not playwright_config:
            st.warning("Enter the App URL in the sidebar first.")
        else:
            run_ids = [tc.id for tc in approved_sel]
            ab1, ab2 = st.columns(2)
            with ab1:
                run_btn = st.button(
                    f"{_mi('play_arrow')} Run Approved ({len(run_ids)})",
                    type="primary",
                    use_container_width=True,
                    disabled=len(run_ids) == 0,
                )
            with ab2:
                report_btn = st.button(
                    f"{_mi('description')} Generate Report",
                    use_container_width=True,
                    disabled=len(state.executions) == 0,
                )

            if run_btn:
                to_run = [tc for tc in state.test_cases if tc.id in run_ids]
                state.executions = []
                executor = ExecutionController(playwright_config, storage, db)

                vision_fn = None
                if use_vision:
                    def vision_fn(screenshot_path: str, expected_results: list):
                        return workflow.analyze_screenshot(screenshot_path, expected_results)

                use_variations = generate_variations or any(tc.variations for tc in to_run)
                total_items = (
                    sum(max(1, len(tc.variations)) for tc in to_run)
                    if use_variations else len(to_run)
                )
                completed = 0
                passed_so_far = 0

                with st.status(f"{_mi('rocket_launch')} Running tests…", expanded=True) as run_status:
                    prog = st.progress(0.0)
                    for tc, result in executor.iter_run(
                        to_run, use_variations=use_variations, vision_fn=vision_fn
                    ):
                        state.executions.append(result)
                        completed += 1
                        if result.status == "passed":
                            passed_so_far += 1
                        icon = _mi(_STATUS_MICON.get(result.status, "help"))
                        vlab = f" [{result.variation_label}]" if getattr(result, "variation_label", None) else ""
                        t    = f"{result.execution_time:.1f}s" if result.execution_time else ""
                        st.write(f"{icon} {tc.title}{vlab}  {t}")
                        prog.progress(completed / total_items)

                    failed_count = completed - passed_so_far
                    label = (
                        f"{_mi('check_circle')} All {completed} tests passed"
                        if failed_count == 0
                        else f"Done — {passed_so_far}/{completed} passed, {failed_count} failed"
                    )
                    run_status.update(
                        label=label,
                        state="complete" if failed_count == 0 else "error",
                        expanded=False,
                    )

                st.success(f"Completed {len(state.executions)} execution(s).")

                if db:
                    try:
                        run_name = ExecutionController.default_run_name(base_url)
                        new_run_id = executor.save_run(
                            run_name,
                            state.requirements,
                            state.test_cases,
                            state.executions,
                            existing_run_id=state.db_run_id,
                        )
                        if new_run_id:
                            state.db_run_id = new_run_id
                        st.info("Results saved to database.", icon=":material/save:")
                    except Exception as db_err:
                        st.warning(f"DB save failed: {db_err}")

            if report_btn:
                with st.spinner("Generating report…"):
                    try:
                        report = report_svc.generate(state.executions, state.requirements)
                        url    = report_svc.upload(report)
                        state.report = {"data": report, "url": url}
                        if state.db_run_id:
                            try:
                                report_svc.save_to_db(state.db_run_id, report)
                            except Exception as db_err:
                                st.warning(f"DB report save failed: {db_err}", icon=":material/warning:")
                        st.success("Report ready")
                    except Exception as e:
                        st.error(str(e))

    # ── Step 4: Results ────────────────────────────────────────────────────
    if state.executions:
        st.header(f"{_mi('counter_4')} Results")
        m = get_metrics(state.executions)

        k1, k2, k3, k4, k5 = st.columns(5)
        k1.metric("Total",      m["total_executions"])
        k2.metric("Passed",  m["passed"])
        k3.metric("Failed",  m["failed"])
        k4.metric("Errors", m["errors"])
        k5.metric("Pass Rate",  f"{m['pass_rate']:.1f}%")

        execs = state.executions
        has_times    = any(e.execution_time for e in execs)
        err_breakdown = _session_error_breakdown(execs)
        chart_cols = st.columns(2 if err_breakdown else 1)

        with chart_cols[0]:
            if has_times:
                st.caption("**Execution time per test**")
                time_data = pd.DataFrame(
                    {
                        "Test":         [e.test_case_id[:18] for e in execs],
                        "Duration (s)": [round(e.execution_time or 0, 2) for e in execs],
                    }
                ).set_index("Test")
                st.bar_chart(time_data["Duration (s)"], height=160)

        if err_breakdown:
            with chart_cols[1]:
                st.caption("**Failure breakdown (this run)**")
                err_df = pd.DataFrame(
                    {"Error type": list(err_breakdown.keys()), "Count": list(err_breakdown.values())}
                ).set_index("Error type")
                st.bar_chart(err_df, height=160)

        flaky_in_session = [e for e in execs if getattr(e, "attempts", 1) > 1]
        if flaky_in_session:
            tc_map = {tc.id: tc.title for tc in state.test_cases}
            st.warning(
                f"**{len(flaky_in_session)} test(s) needed retries** — possible flakiness: "
                + ", ".join(
                    f"`{tc_map.get(e.test_case_id, e.test_case_id)}`"
                    for e in flaky_in_session
                )
            )

        st.divider()

        tc_title_map  = {tc.id: tc.title for tc in state.test_cases}
        has_variations = any(getattr(ex, "variation_label", None) for ex in execs)

        if has_variations:
            groups: dict = defaultdict(list)
            for ex in execs:
                groups[ex.test_case_id].append(ex)
            for tc_id, group_execs in groups.items():
                g_pass  = sum(1 for e in group_execs if e.status == "passed")
                g_total = len(group_execs)
                g_icon  = _mi("check_circle") if g_pass == g_total else _mi("warning") if g_pass > 0 else _mi("cancel")
                title   = tc_title_map.get(tc_id, tc_id)
                with st.expander(f"{g_icon} {title} — {g_pass}/{g_total} passed", expanded=True):
                    for ex in group_execs:
                        _render_result_card(ex, tc_title=tc_title_map.get(ex.test_case_id, ""))
        else:
            for ex in execs:
                _render_result_card(ex, tc_title=tc_title_map.get(ex.test_case_id, ""))

    # ── Step 5: Report ─────────────────────────────────────────────────────
    if state.report:
        st.header(f"{_mi('counter_5')} Report")
        report = state.report["data"]
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')

        report_tabs = st.tabs([f"{_mi('description')} Summary", f"{_mi('map')} Traceability Matrix"])

        with report_tabs[0]:
            st.subheader("Summary")
            st.write(report.summary)
            m = report.metrics
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Total",     m["total_tests"])
            c2.metric("Passed",    m["passed"])
            c3.metric("Failed",    m["failed"])
            c4.metric("Pass Rate", f"{m['pass_rate']:.1f}%")
            st.subheader("Analysis")
            st.write(report.analysis)
            st.subheader("Recommendations")
            for r in report.recommendations:
                st.markdown(f"• {r}")

            dl1, dl2, dl3 = st.columns(3)
            with dl1:
                st.download_button(
                    f"{_mi('download')} Download HTML", report.html_content,
                    file_name=f"report_{ts}.html", mime="text/html", width="stretch",
                )
            with dl2:
                st.download_button(
                    f"{_mi('download')} Download CSV",
                    report_svc.export_csv(state.executions, state.test_cases),
                    file_name=f"report_{ts}.csv", mime="text/csv", width="stretch",
                )
            with dl3:
                st.download_button(
                    f"{_mi('download')} Download JUnit XML",
                    report_svc.export_junit(state.executions, state.test_cases),
                    file_name=f"report_{ts}.xml", mime="application/xml", width="stretch",
                )

        with report_tabs[1]:
            st.subheader("Requirements Traceability Matrix")
            st.caption("Maps each requirement → its test cases → execution results")

            df_matrix = report_svc.build_traceability_matrix(
                state.requirements, state.test_cases, state.executions
            )
            st.dataframe(df_matrix, hide_index=True, use_container_width=True)
            st.download_button(
                f"{_mi('download')} Download Traceability CSV",
                df_matrix.to_csv(index=False),
                file_name=f"traceability_{ts}.csv",
                mime="text/csv",
            )

# ═══════════════════════════════════════════════════════════════════════════
# TAB 2 — Run History & Analytics
# ═══════════════════════════════════════════════════════════════════════════
with history_tab:
    st.header(f"{_mi('folder')} Run History & Analytics")

    if not db:
        st.info("PostgreSQL not configured — run history is unavailable. Set DATABASE_URL to enable.")
    else:
        h_overview, h_trends, h_flaky, h_manage = st.tabs(
            [f"{_mi('bar_chart')} Overview", f"{_mi('trending_up')} Trends",
             f"{_mi('warning')} Flaky Tests", f"{_mi('folder')} Manage Runs"]
        )

        try:
            _history   = db.list_runs(limit=100)
            _analytics = db.get_analytics(limit=60)
        except Exception as _fetch_err:
            st.error(f"History load error: {_fetch_err}")
            _history   = []
            _analytics = {"trend": [], "error_types": {}, "flaky_tests": []}

        _trend       = _analytics["trend"]
        _error_types = _analytics["error_types"]
        _flaky_tests = _analytics["flaky_tests"]

        # ── TAB: Overview ─────────────────────────────────────────────────
        with h_overview:
            if not _history:
                st.info("No saved runs yet. Run some tests from the Test Agent tab.")
            else:
                total_runs   = len(_history)
                total_tests  = sum(r["total"]  for r in _history)
                total_passed = sum(r["passed"] for r in _history)
                avg_pass     = round(total_passed / total_tests * 100, 1) if total_tests else 0.0
                flaky_count  = len(_flaky_tests)

                ov1, ov2, ov3, ov4 = st.columns(4)
                ov1.metric("Total Runs",        total_runs)
                ov2.metric("Tests Executed",    total_tests)
                ov3.metric("Avg Pass Rate",     f"{avg_pass:.1f}%")
                ov4.metric("Flaky Tests (30d)", flaky_count)

                st.divider()
                st.subheader("Recent runs")
                hist_df = pd.DataFrame([
                    {
                        "Run Name":  r["name"],
                        "Date":      r["created_at"].strftime("%Y-%m-%d %H:%M") if r["created_at"] else "—",
                        "Tests":     r["total"],
                        "Passed":    r["passed"],
                        "Failed":    r["total"] - r["passed"],
                        "Pass Rate": f"{r['pass_rate']:.0f}%",
                    }
                    for r in _history[:25]
                ])
                st.dataframe(hist_df, hide_index=True, use_container_width=True)

                if _error_types:
                    st.divider()
                    st.subheader("Failure types (last 30 days)")
                    err_df = pd.DataFrame(
                        {"Error Type": list(_error_types.keys()), "Count": list(_error_types.values())}
                    ).set_index("Error Type").sort_values("Count", ascending=False)
                    st.bar_chart(err_df, height=200)

        # ── TAB: Trends ───────────────────────────────────────────────────
        with h_trends:
            if len(_trend) < 2:
                st.info("Not enough history for trend charts yet — run tests on at least two different days.")
            else:
                trend_df = pd.DataFrame(_trend).set_index("date")
                trend_df.index = pd.to_datetime(trend_df.index)
                trend_df = trend_df.sort_index()

                st.subheader("Pass rate over time (%)")
                st.line_chart(trend_df[["pass_rate"]], height=220)

                st.subheader("Test volume per day")
                st.bar_chart(trend_df[["passed", "failed"]], height=220)

                st.subheader("Runs per day")
                st.bar_chart(trend_df[["run_count"]], height=180)

                with st.expander("Raw trend data", expanded=False):
                    display_trend = trend_df.copy()
                    display_trend.index = display_trend.index.strftime("%Y-%m-%d")
                    st.dataframe(display_trend, use_container_width=True)

        # ── TAB: Flaky Tests ──────────────────────────────────────────────
        with h_flaky:
            st.subheader("Flaky test detection (last 30 days)")
            st.caption(
                "Tests are flagged flaky when they have both passed and failed results "
                "across multiple runs. High flaky rate = unreliable test."
            )

            if not _flaky_tests:
                st.success("No flaky tests detected in the last 30 days.", icon=":material/celebration:")
            else:
                flaky_df = pd.DataFrame(_flaky_tests)
                flaky_df["Flaky Rate"] = flaky_df["flaky_rate"].apply(lambda x: f"{x:.0f}%")
                flaky_df = flaky_df.rename(columns={
                    "tc_id":  "Test Case ID",
                    "total":  "Total Runs",
                    "passed": "Passes",
                    "failed": "Failures",
                })
                flaky_df = flaky_df[["Test Case ID", "Total Runs", "Passes", "Failures", "Flaky Rate"]]
                st.dataframe(
                    flaky_df, hide_index=True, use_container_width=True,
                    column_config={
                        "Flaky Rate": st.column_config.TextColumn("Flaky Rate"),
                        "Failures":   st.column_config.NumberColumn("Failures"),
                    },
                )
                chart_df = (
                    pd.DataFrame(_flaky_tests)
                    .set_index("tc_id")[["passed", "failed"]]
                    .head(10)
                )
                st.subheader("Pass vs Fail counts for flaky tests")
                st.bar_chart(chart_df, height=220)

        # ── TAB: Manage Runs ──────────────────────────────────────────────
        with h_manage:
            mc1, mc2, mc3 = st.columns([3, 2, 1])
            with mc1:
                search_query = st.text_input(
                    f"{_mi('search')} Search", placeholder="Filter by name or URL…", key="hist_search"
                )
            with mc2:
                hist_limit = st.selectbox("Show last", [10, 25, 50, 100], index=1, key="hist_limit")
            with mc3:
                st.write("")
                st.button(f"{_mi('refresh')} Refresh", key="hist_refresh")

            try:
                history = db.list_runs(limit=hist_limit)
                if search_query.strip():
                    q = search_query.strip().lower()
                    history = [r for r in history if q in r["name"].lower()]

                if not history:
                    st.info(
                        "No saved runs found."
                        + (" Try a different search term." if search_query else "")
                    )
                else:
                    st.markdown(f"**{len(history)} run(s)**")
                    st.dataframe(
                        pd.DataFrame([
                            {
                                "ID":        r["id"],
                                "Run Name":  r["name"],
                                "Date":      r["created_at"].strftime("%Y-%m-%d %H:%M")
                                             if r["created_at"] else "—",
                                "Tests":     r["total"],
                                "Passed":    r["passed"],
                                "Pass Rate": f"{r['pass_rate']:.0f}%",
                            }
                            for r in history
                        ]),
                        hide_index=True,
                        use_container_width=True,
                    )

                    st.divider()
                    selected_run_name = st.selectbox(
                        "Select run to manage",
                        options=[r["name"] for r in history],
                        key="hist_select",
                    )
                    selected_run = next((r for r in history if r["name"] == selected_run_name), None)

                    if selected_run:
                        sr = selected_run
                        qa, qb, qc = st.columns(3)
                        qa.metric("Tests",     sr["total"])
                        qb.metric("Passed",    sr["passed"])
                        qc.metric("Pass Rate", f"{sr['pass_rate']:.0f}%")
                        st.markdown(
                            f"{_mi('calendar_today')} {sr['created_at'].strftime('%Y-%m-%d %H:%M') if sr['created_at'] else '—'}"
                        )

                        r_col1, r_col2, r_col3 = st.columns(3)

                        with r_col1:
                            if st.button(f"{_mi('restore')} Load into session", key="hist_load", type="primary"):
                                with st.spinner("Loading run…"):
                                    run_data = db.load_run(selected_run["id"])
                                    state.requirements = run_data["requirements"]
                                    state.test_cases   = run_data["test_cases"]
                                    state.executions   = run_data["executions"]
                                    state.report = (
                                        {"data": run_data["report"], "url": None}
                                        if run_data["report"] else None
                                    )
                                    state.db_run_id = selected_run["id"]
                                    st.success(f"Loaded: {selected_run['name']}")
                                    st.info("Switch to the Test Agent tab to view the run.")

                        with r_col2:
                            new_name = st.text_input(
                                "Rename run", value=selected_run["name"], key="hist_rename_input"
                            )
                            if st.button(f"{_mi('save')} Save name", key="hist_rename_btn"):
                                try:
                                    with db._connect() as conn:
                                        with conn.cursor() as cur:
                                            cur.execute(
                                                "UPDATE qa_runs SET name = %s WHERE id = %s",
                                                (new_name, selected_run["id"]),
                                            )
                                        conn.commit()
                                    st.success("Run renamed.")
                                except Exception as e:
                                    st.error(f"Rename failed: {e}")

                        with r_col3:
                            st.write("")
                            confirm_delete = st.checkbox("Confirm delete", key="hist_del_confirm")
                            if st.button(
                                f"{_mi('delete')} Delete run", key="hist_delete", disabled=not confirm_delete
                            ):
                                try:
                                    db.delete_run(selected_run["id"])
                                    if state.db_run_id == selected_run["id"]:
                                        state.db_run_id = None
                                    st.success(f"Deleted: {selected_run['name']}")
                                except Exception as e:
                                    st.error(f"Delete failed: {e}")

            except Exception as manage_err:
                st.error(f"History load error: {manage_err}")

st.markdown(
    '<hr style="border:none;border-top:0.5px solid #E8E8E4;margin:40px 0 16px 0"/>'
    '<p style="font-size:12px;color:#6B7280;text-align:center">VG Platform — QA Test Agent</p>',
    unsafe_allow_html=True,
)
