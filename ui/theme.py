import streamlit as st


_CSS = r"""<style>
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

/* ── Selectbox value + dropdown menu (readable in ANY base theme) ──────── */
/* Closed control: selected value text + caret */
.stSelectbox div[data-baseweb="select"] > div,
.stSelectbox div[data-baseweb="select"] span,
.stMultiSelect div[data-baseweb="select"] > div {
    color: var(--color-ink) !important;
}
.stSelectbox div[data-baseweb="select"] [class*="placeholder"],
.stMultiSelect div[data-baseweb="select"] [class*="placeholder"] {
    color: var(--color-muted) !important;
}
.stSelectbox div[data-baseweb="select"] svg,
.stMultiSelect div[data-baseweb="select"] svg {
    color: var(--color-ink) !important;
    fill: var(--color-ink) !important;
}
/* Open dropdown menu — rendered in a portal at <body> root, so style globally */
[data-baseweb="popover"] [role="listbox"],
[data-baseweb="popover"] ul {
    background-color: var(--color-bg) !important;
    border: 0.5px solid var(--color-border) !important;
    border-radius: var(--radius-md) !important;
    box-shadow: 0 4px 16px rgba(0,0,0,0.08) !important;
}
[data-baseweb="popover"] li[role="option"],
[data-baseweb="popover"] [role="option"] {
    color: var(--color-ink) !important;
    background-color: var(--color-bg) !important;
    font-size: 13px !important;
    font-family: var(--font-body) !important;
}
[data-baseweb="popover"] li[role="option"]:hover,
[data-baseweb="popover"] li[role="option"][aria-selected="true"],
[data-baseweb="popover"] [role="option"][aria-selected="true"] {
    background-color: var(--color-surface) !important;
    color: var(--color-fg) !important;
}

/* ── Multiselect chips ─────────────────────────────────── */
.stMultiSelect span[data-baseweb="tag"] {
    background-color: var(--color-fg) !important;
    color: #FFFFFF !important;
    border-radius: var(--radius-full) !important;
}
.stMultiSelect span[data-baseweb="tag"] span,
.stMultiSelect span[data-baseweb="tag"] svg {
    color: #FFFFFF !important;
    fill: #FFFFFF !important;
}

/* ── Input value text (force dark in any theme) ────────── */
.stTextInput input, .stTextArea textarea, .stNumberInput input {
    color: var(--color-ink) !important;
}
.stTextInput input::placeholder, .stTextArea textarea::placeholder {
    color: var(--color-muted) !important;
}

/* ── Disabled buttons stay legible (muted, not invisible) ─ */
.stButton > button:disabled,
.stButton > button:disabled *,
.stDownloadButton > button:disabled,
.stDownloadButton > button:disabled * {
    color: var(--color-muted) !important;
    opacity: 1 !important;
}
.stButton > button[kind="primary"]:disabled {
    background-color: var(--color-surface) !important;
    border: 0.5px solid var(--color-border) !important;
}
.stButton > button[kind="primary"]:disabled * {
    color: var(--color-muted) !important;
}
</style>"""


def inject_theme() -> None:
    """Inject the Browserbase design-system stylesheet. Call once near app start."""
    st.markdown(_CSS, unsafe_allow_html=True)


# ── Status constants ───────────────────────────────────────────────────────
# Material Symbol names per status (used for both :material/…: markdown and HTML spans)
STATUS_MICON = {"passed": "check_circle", "failed": "cancel", "error": "warning", "running": "hourglass_empty"}
STATUS_COLOR = {"passed": "#166534", "failed": "#991B1B", "error": "#92400E", "running": "#1E40AF"}
ERR_BADGE_CLASS = {
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

def icon(name: str, size: int = 18, color: str | None = None) -> str:
    """Material Symbol as an inline HTML span — for use inside unsafe_allow_html blocks."""
    style = f"font-size:{size}px"
    if color:
        style += f";color:{color}"
    return f'<span class="msi" style="{style}">{name}</span>'


def mi(name: str) -> str:
    """Material Symbol token for Streamlit-native markdown/labels (headers, buttons, tabs…)."""
    return f":material/{name}:"


def status_micon(status: str, size: int = 20) -> str:
    return icon(STATUS_MICON.get(status, "help"), size=size, color=STATUS_COLOR.get(status, "#6B7280"))


def eyebrow(text: str) -> None:
    """Small uppercase label above a page title (Browserbase eyebrow style)."""
    st.markdown(
        f'<p style="font-size:11px;font-weight:500;text-transform:uppercase;letter-spacing:.06em;'
        f'color:#6B7280;margin:0 0 4px 0">{text}</p>',
        unsafe_allow_html=True,
    )
