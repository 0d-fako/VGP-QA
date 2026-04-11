import sys
import asyncio
import subprocess
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import base64
import io
import json
import tempfile
import os
from collections import defaultdict
from datetime import datetime

import pandas as pd
import streamlit as st

from config import config, configure_logging
from models import PlaywrightConfig
from llm_processor import LLMProcessor, generate_csv_report, generate_junit_xml
from playwright_executor import SyncPlaywrightExecutor, get_metrics, inspect_dom
from azure_storage import AzureStorageManager, LocalStorageManager
from db import DatabaseManager

# Configure logging once at startup
configure_logging()

st.set_page_config(page_title="QA Test Agent", page_icon="🧪", layout="wide")


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
        st.warning(f"⚠️ Browser install issue: {result.stderr[:300]}")
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
.stButton>button { width: 100%; }
.pass { color: #28a745; font-weight: bold; }
.fail { color: #dc3545; font-weight: bold; }
.err  { color: #ffc107; font-weight: bold; }
.ambiguous-badge {
    background: #fff3cd; color: #856404;
    padding: 2px 8px; border-radius: 4px;
    font-size: 0.78em; font-weight: bold;
}
</style>
""", unsafe_allow_html=True)

# ── Session state ──────────────────────────────────────────────────────────
defaults = {
    "requirements":      [],
    "ambiguity_scores":  [],       # REQ 1.4
    "test_cases":        [],
    "selected_tests":    [],
    "executions":        [],
    "report":            None,
    "generating":        False,
    "dom_snapshot":      None,
    "db_run_id":         None,
    "design_context":    None,     # REQ 11: extracted from design asset
    "design_discrepancies": [],    # REQ 11
    "clarifications":    {},       # REQ 1.4: {req_id: clarification_text}
    "external_test_data": None,    # REQ 10.3: uploaded CSV/JSON rows
    "custom_assertions": [],       # REQ 10.4: user-defined assertion rules
}
for k, v in defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ── Module-level helpers ───────────────────────────────────────────────────

ASSERTION_ACTIONS = {
    "check_url", "check_text", "check_element",
    "check_attribute", "check_count",
}


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


# ── Init components ────────────────────────────────────────────────────────
db = None
try:
    config.validate()
    llm = LLMProcessor()
    storage = _get_storage()
    db = _get_db()
    if db is None and DatabaseManager.is_configured():
        st.warning("⚠️ DB connection failed (history disabled).")
    st.session_state.ready = True
except Exception as e:
    st.error(f"Initialisation error: {e}")
    st.session_state.ready = False

# ── Sidebar ────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ Configuration")

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
            if st.button("🌐 Test URL", help="Send a quick HEAD request to verify the URL is reachable before running tests"):
                with st.spinner("Checking…"):
                    try:
                        import requests as _requests
                        resp = _requests.head(base_url, timeout=8, allow_redirects=True)
                        if resp.status_code < 400:
                            st.success(f"✅ Reachable ({resp.status_code})")
                        else:
                            st.warning(f"⚠️ HTTP {resp.status_code} — site may require auth or redirect")
                    except Exception as ping_err:
                        st.error(f"❌ Unreachable: {ping_err}")
        with col_dom:
            if st.button("🔍 Inspect DOM", help="Navigate to the app and extract real CSS selectors"):
                with st.spinner("Inspecting live app DOM…"):
                    try:
                        snapshot = inspect_dom(
                            base_url=base_url,
                            browser_type="chromium",
                            headless=True,
                            timeout=30000,
                            credentials=st.session_state.get("_sidebar_credentials"),
                        )
                        st.session_state.dom_snapshot = snapshot
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

        snap = st.session_state.dom_snapshot
        if snap and not snap.get("error"):
            with st.expander("📋 DOM Snapshot", expanded=False):
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
    # REQ 5.1 — Per-step screenshots toggle
    per_step_ss = st.checkbox(
        "Per-step screenshots",
        value=False,
        help="Capture a screenshot after EVERY step. Useful for debugging failures — slower.",
    )

    st.subheader("Coverage (Phase 2)")
    shared_session     = st.checkbox("Shared browser session", value=False,
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
    st.subheader("📊 Session Usage")
    used = llm.api_call_count if st.session_state.ready else 0
    cap  = config.MAX_API_CALLS_PER_SESSION
    pct  = min(used / cap, 1.0) if cap else 0
    st.progress(pct, text=f"API calls: {used} / {cap}")
    if used >= cap:
        st.error("⛔ Session API call limit reached. Refresh the page to reset.")
    elif used >= cap * 0.8:
        st.warning(f"⚠️ Approaching API call limit ({used}/{cap}).")


# ── Main ───────────────────────────────────────────────────────────────────
st.title("🧪 QA Test Agent")
st.caption("Automated testing powered by Claude AI and Playwright")

if not st.session_state.ready:
    st.stop()

# ── Top-level tabs ─────────────────────────────────────────────────────────
main_tab, history_tab = st.tabs(["🧪 Test Agent", "🗂️ Run History"])

# ═══════════════════════════════════════════════════════════════════════════
# TAB 1 — Test Agent (main workflow)
# ═══════════════════════════════════════════════════════════════════════════
with main_tab:

    # ── Step 1: Input requirements ─────────────────────────────────────────
    st.header("1️⃣ Requirements")
    tab1, tab2, tab3 = st.tabs(["📄 Upload", "📝 Paste", "⚡ Sample"])

    with tab1:
        # REQ 1.5 — multi-file upload
        files = st.file_uploader(
            "Upload docs (PDF, DOCX, TXT, MD) — multiple allowed",
            type=["pdf", "docx", "txt", "md"],
            accept_multiple_files=True,
        )
        if files and st.button("Extract Requirements from Files", type="primary"):
            with st.spinner("Extracting…"):
                all_content = []
                for f in files:
                    fpath = None
                    try:
                        suffix = f".{f.name.rsplit('.', 1)[-1]}"
                        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                            tmp.write(f.getvalue())
                            fpath = tmp.name
                        if f.name.endswith(".pdf"):
                            import pdfplumber
                            blocks = []
                            with pdfplumber.open(fpath) as pdf:
                                for page in pdf.pages:
                                    t = page.extract_text()
                                    if t:
                                        blocks.append(t)
                            all_content.append(f"### {f.name}\n" + "\n".join(blocks))
                        elif f.name.endswith(".docx"):
                            import docx
                            doc = docx.Document(fpath)
                            all_content.append(f"### {f.name}\n" + "\n".join(p.text for p in doc.paragraphs))
                        else:
                            with open(fpath, encoding="utf-8", errors="replace") as fh:
                                all_content.append(f"### {f.name}\n" + fh.read())
                    except Exception as e:
                        st.error(f"{f.name}: {e}")
                    finally:
                        if fpath and os.path.exists(fpath):
                            os.unlink(fpath)

                if all_content:
                    merged_content = "\n\n".join(all_content)
                    try:
                        st.session_state.requirements = llm.analyze_requirements(merged_content)
                        st.session_state.ambiguity_scores = []
                        st.success(f"Extracted {len(st.session_state.requirements)} requirements from {len(files)} file(s)")
                    except Exception as e:
                        st.error(str(e))

    with tab2:
        text = st.text_area("Paste requirements or user stories:", height=250)
        if st.button("Analyze", type="primary"):
            if text.strip():
                with st.spinner("Analyzing…"):
                    try:
                        st.session_state.requirements    = llm.analyze_requirements(text)
                        st.session_state.ambiguity_scores = []
                        st.success(f"Extracted {len(st.session_state.requirements)} requirements")
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
                    st.session_state.requirements    = llm.analyze_requirements(sample)
                    st.session_state.ambiguity_scores = []
                    st.success(f"Loaded {len(st.session_state.requirements)} requirements")
                except Exception as e:
                    st.error(str(e))

    # ── REQ 1.4 — Ambiguity flagging ──────────────────────────────────────
    if st.session_state.requirements:
        col_req, col_amb = st.columns([3, 1])
        with col_amb:
            if st.button("🔎 Check Ambiguity", help="Score each requirement for clarity and testability"):
                with st.spinner("Analysing requirement clarity…"):
                    try:
                        st.session_state.ambiguity_scores = llm.flag_ambiguous_requirements(
                            st.session_state.requirements
                        )
                    except Exception as e:
                        st.error(f"Ambiguity check failed: {e}")

        # Build a lookup: req_id → score dict
        amb_lookup = {s["requirement_id"]: s for s in st.session_state.ambiguity_scores}

        for req in st.session_state.requirements:
            amb = amb_lookup.get(req.id, {})
            score = amb.get("clarity_score", 1.0)
            is_ambiguous = score < 0.7

            badge = (
                f' <span class="ambiguous-badge">⚠️ Ambiguous ({score:.0%})</span>'
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
                        st.info(f"💡 **Suggestion:** {amb['suggestion']}")
                    # REQ 1.4 — clarification text box
                    clarity_key = f"clarify_{req.id}"
                    existing    = st.session_state.clarifications.get(req.id, "")
                    new_text    = st.text_area(
                        "Add clarification (appended to requirement before generation):",
                        value=existing,
                        key=clarity_key,
                        height=80,
                    )
                    if new_text != existing:
                        st.session_state.clarifications[req.id] = new_text

    # ── REQ 11 — Optional Design Asset Upload ─────────────────────────────
    if st.session_state.requirements:
        st.markdown("---")
        with st.expander("🎨 Design Assets (Optional — REQ 11)", expanded=False):
            st.caption(
                "Upload a screenshot or mockup of your app's UI. Claude will analyse it against "
                "your requirements and inject visual validation steps into generated tests."
            )
            design_file = st.file_uploader(
                "Upload design image (PNG, JPG, WEBP)", type=["png", "jpg", "jpeg", "webp"], key="design_upload"
            )
            figma_url = st.text_input("Or paste a Figma/image URL", placeholder="https://…", key="figma_url")

            if st.button("🔍 Analyse Design", key="analyse_design"):
                image_b64  = None
                media_type = "image/png"
                with st.spinner("Analysing design asset…"):
                    try:
                        if design_file:
                            raw_bytes  = design_file.read()
                            image_b64  = base64.standard_b64encode(raw_bytes).decode()
                            ext        = design_file.name.rsplit(".", 1)[-1].lower()
                            media_type = f"image/{'jpeg' if ext in ('jpg','jpeg') else ext}"
                        elif figma_url.strip():
                            import requests as _req
                            resp       = _req.get(figma_url.strip(), timeout=15)
                            resp.raise_for_status()
                            image_b64  = base64.standard_b64encode(resp.content).decode()
                            ct         = resp.headers.get("content-type", "image/png").split(";")[0]
                            media_type = ct

                        if image_b64:
                            result = llm.analyze_design_asset(
                                image_b64,
                                st.session_state.requirements,
                                media_type=media_type,
                            )
                            st.session_state.design_context       = result.get("design_context", "")
                            st.session_state.design_discrepancies = result.get("discrepancies", [])

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

            if st.session_state.design_context:
                st.success("✅ Design context ready — will be injected into next test generation.")
                if st.button("Clear design context", key="clear_design"):
                    st.session_state.design_context       = None
                    st.session_state.design_discrepancies = []

    # ── REQ 10.3 — External test data upload ──────────────────────────────
    if st.session_state.requirements:
        with st.expander("📂 External Test Data (Optional — REQ 10.3)", expanded=False):
            st.caption("Upload a CSV or JSON file with test data rows. Each row will be used as a variation.")
            td_file = st.file_uploader("Upload CSV or JSON", type=["csv", "json"], key="testdata_upload")
            if td_file:
                try:
                    if td_file.name.endswith(".csv"):
                        df = pd.read_csv(td_file)
                        st.session_state.external_test_data = df.to_dict(orient="records")
                    else:
                        raw = json.loads(td_file.read().decode())
                        if isinstance(raw, list):
                            st.session_state.external_test_data = raw
                        elif isinstance(raw, dict):
                            st.session_state.external_test_data = [raw]
                        else:
                            st.error("JSON must be an array of objects or a single object.")
                    if st.session_state.external_test_data:
                        st.success(f"Loaded {len(st.session_state.external_test_data)} data row(s).")
                        st.dataframe(pd.DataFrame(st.session_state.external_test_data).head(5), hide_index=True)
                except Exception as e:
                    st.error(f"Failed to parse test data: {e}")
            if st.session_state.external_test_data:
                if st.button("Clear test data", key="clear_td"):
                    st.session_state.external_test_data = None

    # ── REQ 10.4 — Custom assertions ─────────────────────────────────────
    if st.session_state.requirements:
        with st.expander("✏️ Custom Assertion Rules (Optional — REQ 10.4)", expanded=False):
            st.caption(
                "Add custom text or attribute assertions. These are injected as `check_text` / "
                "`check_attribute` steps into every generated test case."
            )
            new_rule_text = st.text_input(
                "Assertion rule", placeholder='e.g. "Welcome" or "aria-label=Submit"', key="custom_assert_input"
            )
            if st.button("➕ Add Rule", key="add_custom_assert"):
                rule = new_rule_text.strip()
                if rule and rule not in st.session_state.custom_assertions:
                    st.session_state.custom_assertions.append(rule)

            if st.session_state.custom_assertions:
                st.markdown("**Active rules:**")
                to_remove = []
                for i, rule in enumerate(st.session_state.custom_assertions):
                    c1, c2 = st.columns([5, 1])
                    c1.code(rule, language=None)
                    if c2.button("✕", key=f"remove_rule_{i}"):
                        to_remove.append(rule)
                for r in to_remove:
                    st.session_state.custom_assertions.remove(r)

    # ── Step 2: Generate test cases ────────────────────────────────────────
    if st.session_state.requirements:
        st.header("2️⃣ Generate Test Cases")
        max_tc = st.slider("Max test cases", 1, 10, 5)

        snap = st.session_state.dom_snapshot
        if snap and not snap.get("error"):
            st.info(
                f"🔍 DOM snapshot available ({len(snap.get('inputs', []))} inputs, "
                f"{len(snap.get('buttons', []))} buttons) — real selectors will be injected."
            )
        elif base_url:
            st.caption("💡 Tip: Click **Inspect DOM** in the sidebar to inject real selectors and reduce selector failures.")

        if st.session_state.design_context:
            st.info("🎨 Design context will be injected into test generation.")
        if st.session_state.custom_assertions:
            st.info(f"✏️ {len(st.session_state.custom_assertions)} custom assertion rule(s) will be appended to every test.")
        if st.session_state.external_test_data:
            st.info(f"📂 {len(st.session_state.external_test_data)} external data row(s) loaded — will be used as variations.")

        _limit_hit = llm.rate_limit_exceeded()
        if _limit_hit:
            st.error("⛔ Session API call limit reached. Refresh the page to generate more test cases.")
        if st.button("🧪 Generate Test Cases", type="primary", width="stretch", disabled=_limit_hit):
            if not st.session_state.generating:
                st.session_state.generating = True
                with st.spinner("Generating…"):
                    try:
                        # Apply clarifications to requirement descriptions before generating
                        reqs_for_gen = []
                        for req in st.session_state.requirements:
                            clarification = st.session_state.clarifications.get(req.id, "").strip()
                            if clarification:
                                import copy
                                req_copy = copy.copy(req)
                                req_copy.description = req.description + f"\n\nClarification: {clarification}"
                                reqs_for_gen.append(req_copy)
                            else:
                                reqs_for_gen.append(req)

                        tcs = llm.generate_test_cases(
                            reqs_for_gen,
                            username_selector=u_sel,
                            password_selector=p_sel,
                            submit_selector=s_sel,
                            max_cases=max_tc,
                            dom_snapshot=st.session_state.dom_snapshot,
                            generate_variations=generate_variations,
                            design_context=st.session_state.design_context,
                        )

                        # REQ 10.3 — inject external data rows as extra variations
                        ext_data = st.session_state.external_test_data
                        if ext_data and tcs:
                            for tc in tcs:
                                for i, row in enumerate(ext_data):
                                    tc.variations.append({
                                        "label":           f"ext-data-row-{i+1}",
                                        "data":            dict(row),
                                        "expected_results": tc.expected_results,
                                    })

                        # REQ 10.4 — append custom assertion steps to every test case
                        from models import TestStep
                        for tc in tcs:
                            for rule in st.session_state.custom_assertions:
                                if "=" in rule:
                                    # Treat as check_attribute: "attr=value" on body
                                    tc.steps.append(TestStep(action="check_attribute", selector="body", value=rule))
                                else:
                                    tc.steps.append(TestStep(action="check_text", value=rule))

                        st.session_state.test_cases    = tcs
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
                        st.session_state.generating = False

    # ── Step 3: Select & Execute ───────────────────────────────────────────
    if st.session_state.test_cases:
        st.header("3️⃣ Select & Edit Tests")

        selected_ids = []
        for tc in st.session_state.test_cases:
            col_chk, col_title = st.columns([1, 9])
            with col_chk:
                checked = st.checkbox("", value=True, key=f"chk_{tc.id}", label_visibility="collapsed")
            with col_title:
                st.markdown(f"**{tc.title}**  `{tc.id}`")
            if checked:
                selected_ids.append(tc.id)

            # REQ 8.2 — editable step table via st.data_editor
            with st.expander(f"✏️ Edit steps — {tc.title}", expanded=False):
                st.markdown("**Steps** *(edit inline — Action, Selector, Value columns are editable)*")

                step_dicts = [
                    {
                        "#":       i + 1,
                        "Type":    "🔍 Assert" if s.action in ASSERTION_ACTIONS else "▶ Action",
                        "Action":  s.action,
                        "Selector": s.selector or "",
                        "Value":   s.value or "",
                        "Force":   s.force,
                    }
                    for i, s in enumerate(tc.steps)
                ]
                edited = st.data_editor(
                    pd.DataFrame(step_dicts),
                    key=f"editor_{tc.id}",
                    num_rows="dynamic",
                    hide_index=True,
                    column_config={
                        "#":        st.column_config.NumberColumn(disabled=True, width="small"),
                        "Type":     st.column_config.TextColumn(disabled=True, width="small"),
                        "Action":   st.column_config.SelectboxColumn(
                            options=sorted([
                                "goto", "fill", "click", "check", "press",
                                "wait_for_selector", "wait_for_load_state", "wait_for_timeout",
                                "scroll_to", "hover", "select", "click_text",
                                "check_url", "check_text", "check_element",
                                "check_attribute", "check_count",
                            ]),
                            width="medium",
                        ),
                        "Selector": st.column_config.TextColumn(width="medium"),
                        "Value":    st.column_config.TextColumn(width="medium"),
                        "Force":    st.column_config.CheckboxColumn(width="small"),
                    },
                )

                if st.button(f"💾 Apply edits — {tc.title}", key=f"apply_{tc.id}"):
                    from models import TestStep as _TS
                    new_steps = []
                    for _, row in edited.iterrows():
                        action = str(row.get("Action", "")).strip()
                        if not action:
                            continue
                        new_steps.append(_TS(
                            action=action,
                            selector=str(row.get("Selector", "")).strip() or None,
                            value=str(row.get("Value", "")).strip() or None,
                            force=bool(row.get("Force", False)),
                        ))
                    if new_steps:
                        tc.steps = new_steps
                        st.success(f"Steps updated — {len(new_steps)} step(s) saved.")
                    else:
                        st.warning("No valid steps remaining after edit.")

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

                # REQ 8.4 — per-test regeneration button
                if st.button(f"🔄 Regenerate this test", key=f"regen_{tc.id}"):
                    req_match = next(
                        (r for r in st.session_state.requirements if r.id == tc.requirement_id), None
                    )
                    if req_match:
                        with st.spinner(f"Regenerating {tc.title}…"):
                            try:
                                new_tcs = llm.generate_test_cases(
                                    [req_match],
                                    username_selector=u_sel,
                                    password_selector=p_sel,
                                    submit_selector=s_sel,
                                    max_cases=1,
                                    dom_snapshot=st.session_state.dom_snapshot,
                                    design_context=st.session_state.design_context,
                                )
                                if new_tcs:
                                    idx = next(
                                        (i for i, t in enumerate(st.session_state.test_cases) if t.id == tc.id), None
                                    )
                                    if idx is not None:
                                        new_tcs[0].id = tc.id   # preserve original ID
                                        st.session_state.test_cases[idx] = new_tcs[0]
                                    st.success(f"Regenerated: {tc.title}")
                            except Exception as e:
                                st.error(f"Regeneration failed: {e}")
                    else:
                        st.warning("Could not find the source requirement for this test case.")

        if not playwright_config:
            st.warning("Enter the App URL in the sidebar first.")
        else:
            col1, col2 = st.columns(2)
            with col1:
                run_btn = st.button(
                    "▶️ Run Selected Tests",
                    type="primary",
                    width="stretch",
                    disabled=len(selected_ids) == 0,
                )
            with col2:
                report_btn = st.button(
                    "📄 Generate Report",
                    width="stretch",
                    disabled=len(st.session_state.executions) == 0,
                )

            if run_btn:
                to_run = [tc for tc in st.session_state.test_cases if tc.id in selected_ids]
                st.session_state.executions = []
                executor = SyncPlaywrightExecutor(playwright_config)

                vision_fn = None
                if use_vision:
                    def vision_fn(screenshot_path: str, expected_results: list):
                        return llm.analyze_screenshot(screenshot_path, expected_results)

                progress = st.progress(0)
                status   = st.empty()

                if generate_variations or any(tc.variations for tc in to_run):
                    total_items = sum(max(1, len(tc.variations)) for tc in to_run)
                    completed = 0
                    for tc in to_run:
                        status.info(f"Running variations for: {tc.title}")
                        variation_results = executor.execute_test_case_with_variations(tc, vision_fn=vision_fn)
                        for res in variation_results:
                            st.session_state.executions.append(res)
                            storage.upload_execution_evidence(res)
                            completed += 1
                            progress.progress(completed / total_items)
                else:
                    for i, tc in enumerate(to_run):
                        status.info(f"Running {i + 1}/{len(to_run)}: {tc.title}")
                        result = executor.execute_test_case(tc, vision_fn=vision_fn)
                        st.session_state.executions.append(result)
                        storage.upload_execution_evidence(result)
                        progress.progress((i + 1) / len(to_run))

                status.empty()
                st.success(f"Done — {len(st.session_state.executions)} executions completed.")

                if db:
                    try:
                        run_name = f"{datetime.now().strftime('%Y-%m-%d %H:%M')} — {base_url}"
                        if st.session_state.db_run_id:
                            db.update_run(st.session_state.db_run_id, executions=st.session_state.executions)
                        else:
                            run_id = db.save_run(
                                name=run_name,
                                requirements=st.session_state.requirements,
                                test_cases=st.session_state.test_cases,
                                executions=st.session_state.executions,
                            )
                            st.session_state.db_run_id = run_id
                        st.info("💾 Results saved to database.")
                    except Exception as db_err:
                        st.warning(f"DB save failed: {db_err}")

            if report_btn:
                with st.spinner("Generating report…"):
                    try:
                        report = llm.generate_test_report(
                            st.session_state.executions,
                            st.session_state.requirements,
                        )
                        url = storage.upload_test_report(
                            report.html_content,
                            f"report_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                        )
                        st.session_state.report = {"data": report, "url": url}
                        if db and st.session_state.db_run_id:
                            try:
                                db.update_run(st.session_state.db_run_id, report=report)
                            except Exception as db_err:
                                st.warning(f"⚠️ DB report save failed: {db_err}")
                        st.success("Report ready")
                    except Exception as e:
                        st.error(str(e))

    # ── Step 4: Results ────────────────────────────────────────────────────
    if st.session_state.executions:
        st.header("4️⃣ Results")
        m = get_metrics(st.session_state.executions)

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total",      m["total_executions"])
        c2.metric("Passed ✅",  m["passed"])
        c3.metric("Failed ❌",  m["failed"])
        c4.metric("Errors ⚠️", m["errors"])

        has_variations = any(
            getattr(ex, "variation_label", None) for ex in st.session_state.executions
        )

        if has_variations:
            groups: dict = defaultdict(list)
            for ex in st.session_state.executions:
                groups[ex.test_case_id].append(ex)
            for tc_id, execs in groups.items():
                g_pass  = sum(1 for e in execs if e.status == "passed")
                g_total = len(execs)
                g_icon  = "✅" if g_pass == g_total else "⚠️" if g_pass > 0 else "❌"
                with st.expander(f"{g_icon} {tc_id} — {g_pass}/{g_total} passed", expanded=True):
                    for ex in execs:
                        icon = "✅" if ex.status == "passed" else "❌" if ex.status == "failed" else "⚠️"
                        t    = f"{ex.execution_time:.2f}s" if ex.execution_time is not None else "N/A"
                        vlab = f" [{ex.variation_label}]" if ex.variation_label else ""
                        rn   = f" (attempt {ex.attempts})" if ex.attempts > 1 else ""
                        eb   = f" `{ex.error_type}`" if ex.error_type else ""
                        st.markdown(f"{icon} **{ex.status.upper()}**{vlab}{rn}{eb} — {t}")
                        _render_error_message(ex)
                        vv = getattr(ex, "vision_verdict", None)
                        if vv:
                            v_icon = "✅" if vv.get("passed") else "❌"
                            st.caption(
                                f"👁 Vision: {v_icon} "
                                f"(confidence {vv.get('confidence',0):.0%}) — {vv.get('explanation','')}"
                            )
                        if ex.screenshots:
                            cols = st.columns(min(3, len(ex.screenshots)))
                            for idx, s in enumerate(ex.screenshots):
                                if os.path.exists(s):
                                    cols[idx % 3].image(s, width="stretch")
        else:
            for ex in st.session_state.executions:
                icon = "✅" if ex.status == "passed" else "❌" if ex.status == "failed" else "⚠️"
                t    = f"{ex.execution_time:.2f}s" if ex.execution_time is not None else "N/A"
                rn   = f" · attempt {ex.attempts}" if ex.attempts > 1 else ""
                eb   = f" [{ex.error_type}]" if ex.error_type else ""
                with st.expander(f"{icon} {ex.test_case_id} — {ex.status.upper()}{eb} ({t}){rn}"):
                    _render_error_message(ex)
                    vv = getattr(ex, "vision_verdict", None)
                    if vv:
                        v_icon = "✅" if vv.get("passed") else "❌"
                        st.info(
                            f"👁 Vision verification: {v_icon} "
                            f"(confidence {vv.get('confidence',0):.0%})\n\n{vv.get('explanation','')}"
                        )
                    if ex.screenshots:
                        cols = st.columns(min(3, len(ex.screenshots)))
                        for i, s in enumerate(ex.screenshots):
                            if os.path.exists(s):
                                cols[i % 3].image(s, width="stretch")

    # ── Step 5: Report ─────────────────────────────────────────────────────
    if st.session_state.report:
        st.header("5️⃣ Report")
        report = st.session_state.report["data"]

        report_tabs = st.tabs(["📋 Summary", "🗺️ Traceability Matrix"])

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
            ts   = datetime.now().strftime('%Y%m%d_%H%M%S')
            dl1, dl2, dl3 = st.columns(3)
            with dl1:
                st.download_button(
                    "💾 Download HTML", report.html_content,
                    file_name=f"report_{ts}.html", mime="text/html", width="stretch",
                )
            with dl2:
                csv_data = generate_csv_report(st.session_state.executions, st.session_state.test_cases)
                st.download_button(
                    "📊 Download CSV", csv_data,
                    file_name=f"report_{ts}.csv", mime="text/csv", width="stretch",
                )
            with dl3:
                xml_data = generate_junit_xml(st.session_state.executions, st.session_state.test_cases)
                st.download_button(
                    "🔖 Download JUnit XML", xml_data,
                    file_name=f"report_{ts}.xml", mime="application/xml", width="stretch",
                )

        # REQ 6.5 / REQ 2.5 — Traceability matrix
        with report_tabs[1]:
            st.subheader("Requirements Traceability Matrix")
            st.caption("Maps each requirement → its test cases → execution results")

            exec_by_tc = {ex.test_case_id: ex for ex in st.session_state.executions}

            matrix_rows = []
            for req in st.session_state.requirements:
                linked_tcs = [tc for tc in st.session_state.test_cases if tc.requirement_id == req.id]
                if not linked_tcs:
                    matrix_rows.append({
                        "Requirement":  f"[{req.id}] {req.title}",
                        "Test Case":    "—",
                        "TC ID":        "—",
                        "Status":       "⚪ Not tested",
                        "Duration":     "—",
                        "Error Type":   "—",
                    })
                else:
                    for tc in linked_tcs:
                        ex = exec_by_tc.get(tc.id)
                        if ex:
                            icon = {"passed": "✅", "failed": "❌", "error": "⚠️"}.get(ex.status, "❓")
                            status_str = f"{icon} {ex.status.upper()}"
                            dur = f"{ex.execution_time:.2f}s" if ex.execution_time else "—"
                            err = ex.error_type or "—"
                        else:
                            status_str = "⚪ Not run"
                            dur        = "—"
                            err        = "—"
                        matrix_rows.append({
                            "Requirement": f"[{req.id}] {req.title}",
                            "Test Case":   tc.title,
                            "TC ID":       tc.id,
                            "Status":      status_str,
                            "Duration":    dur,
                            "Error Type":  err,
                        })

            df_matrix = pd.DataFrame(matrix_rows)
            st.dataframe(df_matrix, hide_index=True, use_container_width=True)

            # Download traceability CSV
            st.download_button(
                "📥 Download Traceability CSV",
                df_matrix.to_csv(index=False),
                file_name=f"traceability_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                mime="text/csv",
            )

# ═══════════════════════════════════════════════════════════════════════════
# TAB 2 — Run History (REQ: dedicated history page)
# ═══════════════════════════════════════════════════════════════════════════
with history_tab:
    st.header("🗂️ Run History")

    if not db:
        st.info("PostgreSQL not configured — run history is unavailable. Set DATABASE_URL to enable.")
    else:
        # Controls row
        h_col1, h_col2, h_col3 = st.columns([3, 2, 1])
        with h_col1:
            search_query = st.text_input("🔍 Search runs", placeholder="Filter by name or URL…", key="hist_search")
        with h_col2:
            hist_limit = st.selectbox("Show last", [10, 25, 50, 100], index=1, key="hist_limit")
        with h_col3:
            st.write("")  # spacer
            refresh_hist = st.button("🔄 Refresh", key="hist_refresh")

        try:
            history = db.list_runs(limit=hist_limit)

            # Filter by search
            if search_query.strip():
                q = search_query.strip().lower()
                history = [r for r in history if q in r["name"].lower()]

            if not history:
                st.info("No saved runs found." + (" Try a different search term." if search_query else ""))
            else:
                st.markdown(f"**{len(history)} run(s) found**")

                # Build display table
                hist_df = pd.DataFrame([
                    {
                        "ID":         r["id"],
                        "Run Name":   r["name"],
                        "Date":       r["created_at"].strftime("%Y-%m-%d %H:%M") if r["created_at"] else "—",
                        "Tests":      r["total"],
                        "Passed":     r["passed"],
                        "Pass Rate":  f"{r['pass_rate']:.0f}%",
                    }
                    for r in history
                ])
                st.dataframe(hist_df, hide_index=True, use_container_width=True)

                st.markdown("---")
                st.subheader("Load or manage a run")

                selected_run_name = st.selectbox(
                    "Select run",
                    options=[r["name"] for r in history],
                    key="hist_select",
                )
                selected_run = next((r for r in history if r["name"] == selected_run_name), None)

                if selected_run:
                    r_col1, r_col2, r_col3 = st.columns(3)

                    with r_col1:
                        if st.button("↩ Load into session", key="hist_load", type="primary"):
                            with st.spinner("Loading run…"):
                                run_data = db.load_run(selected_run["id"])
                                st.session_state.requirements = run_data["requirements"]
                                st.session_state.test_cases   = run_data["test_cases"]
                                st.session_state.executions   = run_data["executions"]
                                st.session_state.report       = (
                                    {"data": run_data["report"], "url": None}
                                    if run_data["report"] else None
                                )
                                st.session_state.db_run_id    = selected_run["id"]
                                st.success(f"Loaded: {selected_run['name']}")
                                st.info("Switch to the 🧪 Test Agent tab to see the loaded run.")

                    with r_col2:
                        # Rename run
                        new_name = st.text_input(
                            "Rename run", value=selected_run["name"], key="hist_rename_input"
                        )
                        if st.button("✏️ Save name", key="hist_rename_btn"):
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
                        # Delete with confirmation
                        confirm_delete = st.checkbox("Confirm delete", key="hist_del_confirm")
                        if st.button("🗑️ Delete run", key="hist_delete", disabled=not confirm_delete):
                            try:
                                db.delete_run(selected_run["id"])
                                if st.session_state.db_run_id == selected_run["id"]:
                                    st.session_state.db_run_id = None
                                st.success(f"Deleted run: {selected_run['name']}")
                            except Exception as e:
                                st.error(f"Delete failed: {e}")

                    # Quick metrics for selected run
                    st.markdown("---")
                    st.markdown(
                        f"**{selected_run['name']}** · "
                        f"{selected_run['passed']}/{selected_run['total']} passed "
                        f"({selected_run['pass_rate']:.0f}%) · "
                        f"{selected_run['created_at'].strftime('%Y-%m-%d %H:%M') if selected_run['created_at'] else '—'}"
                    )

        except Exception as hist_err:
            st.error(f"History load error: {hist_err}")

st.markdown("---")
st.caption("Built with ❤️ by VG Platform")
