import sys
import asyncio
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import streamlit as st
import os
import tempfile
from datetime import datetime

from config import config, configure_logging
from models import PlaywrightConfig
from llm_processor import LLMProcessor
from playwright_executor import SyncPlaywrightExecutor, get_metrics, inspect_dom
from azure_storage import AzureStorageManager, LocalStorageManager

# Configure logging once at startup
configure_logging()

st.set_page_config(page_title="QA Test Agent", page_icon="🧪", layout="wide")

st.markdown("""
<style>
.stButton>button { width: 100%; }
.pass { color: #28a745; font-weight: bold; }
.fail { color: #dc3545; font-weight: bold; }
.err  { color: #ffc107; font-weight: bold; }
</style>
""", unsafe_allow_html=True)

# ── Session state ──────────────────────────────────────────────────────────
defaults = {
    "requirements": [],
    "test_cases": [],
    "selected_tests": [],
    "executions": [],
    "report": None,
    "generating": False,
    "dom_snapshot": None,       # Phase 1: live app DOM inspection result
}
for k, v in defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ── Init components ────────────────────────────────────────────────────────
try:
    config.validate()
    llm = LLMProcessor()
    storage = (
        AzureStorageManager()
        if config.AZURE_STORAGE_CONNECTION_STRING
        else LocalStorageManager()
    )
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

    # Phase 1 — DOM inspection button
    if base_url:
        if st.button("🔍 Inspect App DOM", help="Navigate to the app and extract real CSS selectors for better test generation"):
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
                        btn_count = len(snapshot.get("buttons", []))
                        st.success(
                            f"Inspected: {snapshot.get('title', base_url)} "
                            f"({input_count} inputs, {btn_count} buttons found)"
                        )
                except Exception as e:
                    st.error(f"DOM inspection failed: {e}")

        # Show a summary of the DOM snapshot when available
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

    # Phase 1 — Retry logic
    st.subheader("Reliability (Phase 1)")
    max_retries = st.slider(
        "Retry on timeout",
        min_value=0, max_value=3, value=0,
        help="Retry the entire test if a step times out. 0 = no retry.",
    )
    use_vision = st.checkbox(
        "Vision verification",
        value=False,
        help="Use Claude vision to verify the final screenshot against expected results.",
    )

    # Phase 2 — Coverage settings
    st.subheader("Coverage (Phase 2)")
    shared_session = st.checkbox(
        "Shared browser session",
        value=False,
        help="Authenticate once and share the browser context across all tests in the suite.",
    )
    generate_variations = st.checkbox(
        "Generate test variations",
        value=False,
        help="Ask Claude to generate 2-3 parameterized variations (boundary/negative) per test case.",
    )

    # ── Authentication ────────────────────────────────────────────────────
    st.subheader("Authentication")

    # Phase 2 — auth type selector
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
                u_sel = st.text_input("Username selector", value="#username")
                username = st.text_input("Username")
            with col2:
                p_sel = st.text_input("Password selector", value="#password")
                password = st.text_input("Password", type="password")
            s_sel = st.text_input("Submit selector (optional)", placeholder="button[type='submit']")
            if username and password:
                credentials = {
                    "login_url": login_url or "",
                    "username_selector": u_sel,
                    "password_selector": p_sel,
                    "submit_selector": s_sel,
                    "username": username,
                    "password": password,
                }

        elif auth_type == "cookie":
            st.caption("Paste a JSON array of cookie objects, e.g. [{\"name\": \"session\", \"value\": \"...\", \"domain\": \"...\"}]")
            cookie_json = st.text_area("Cookies (JSON)", height=100)
            if cookie_json.strip():
                credentials = {"cookies": cookie_json.strip()}

        elif auth_type == "token":
            token_val = st.text_input("Bearer token", type="password")
            if token_val.strip():
                credentials = {"token": token_val.strip()}

    # Stash credentials so DOM inspect button can use them
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
        )

# ── Main ───────────────────────────────────────────────────────────────────
st.title("🧪 QA Test Agent")
st.caption("Automated testing powered by Claude AI and Playwright")

if not st.session_state.ready:
    st.stop()

# ── Step 1: Input requirements ─────────────────────────────────────────────
st.header("1️⃣ Requirements")
tab1, tab2, tab3 = st.tabs(["📄 Upload", "📝 Paste", "⚡ Sample"])

with tab1:
    f = st.file_uploader("Upload doc (PDF, DOCX, TXT, MD)", type=["pdf", "docx", "txt", "md"])
    if f:
        with st.spinner("Extracting..."):
            fpath = None
            try:
                suffix = f".{f.name.rsplit('.', 1)[-1]}"
                with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                    tmp.write(f.getvalue())
                    fpath = tmp.name

                if f.name.endswith(".pdf"):
                    from unstructured.partition.pdf import partition_pdf
                    content = "\n".join(str(e) for e in partition_pdf(filename=fpath))
                elif f.name.endswith(".docx"):
                    from unstructured.partition.docx import partition_docx
                    content = "\n".join(str(e) for e in partition_docx(filename=fpath))
                else:
                    with open(fpath, encoding="utf-8") as fh:
                        content = fh.read()

                st.session_state.requirements = llm.analyze_requirements(content)
                st.success(f"Extracted {len(st.session_state.requirements)} requirements")
            except Exception as e:
                st.error(str(e))
            finally:
                if fpath and os.path.exists(fpath):
                    os.unlink(fpath)

with tab2:
    text = st.text_area("Paste requirements or user stories:", height=250)
    if st.button("Analyze", type="primary"):
        if text.strip():
            with st.spinner("Analyzing..."):
                try:
                    st.session_state.requirements = llm.analyze_requirements(text)
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
        with st.spinner("Processing..."):
            try:
                st.session_state.requirements = llm.analyze_requirements(sample)
                st.success(f"Loaded {len(st.session_state.requirements)} requirements")
            except Exception as e:
                st.error(str(e))

# ── Step 2: Generate test cases ────────────────────────────────────────────
if st.session_state.requirements:
    st.header("2️⃣ Generate Test Cases")

    for req in st.session_state.requirements:
        with st.expander(f"{req.title} ({req.id})"):
            st.write(req.description)
            for c in req.acceptance_criteria:
                st.markdown(f"- {c}")

    max_tc = st.slider("Max test cases", 1, 10, 5)

    # Show DOM snapshot status inline
    snap = st.session_state.dom_snapshot
    if snap and not snap.get("error"):
        st.info(
            f"🔍 DOM snapshot available ({len(snap.get('inputs', []))} inputs, "
            f"{len(snap.get('buttons', []))} buttons) — real selectors will be injected into the prompt."
        )
    elif base_url:
        st.caption("💡 Tip: Click **Inspect App DOM** in the sidebar to inject real selectors and reduce selector failures.")

    if st.button("🧪 Generate Test Cases", type="primary", width='stretch'):
        if not st.session_state.generating:
            st.session_state.generating = True
            with st.spinner("Generating..."):
                try:
                    tcs = llm.generate_test_cases(
                        st.session_state.requirements,
                        username_selector=u_sel,
                        password_selector=p_sel,
                        submit_selector=s_sel,
                        max_cases=max_tc,
                        dom_snapshot=st.session_state.dom_snapshot,      # Phase 1
                        generate_variations=generate_variations,          # Phase 2
                    )
                    st.session_state.test_cases = tcs
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

# ── Step 3: Select & execute ───────────────────────────────────────────────
if st.session_state.test_cases:
    st.header("3️⃣ Select & Execute Tests")

    st.markdown("**Select tests to run:**")
    selected_ids = []
    for tc in st.session_state.test_cases:
        checked = st.checkbox(f"{tc.title}", value=True, key=f"chk_{tc.id}")
        if checked:
            selected_ids.append(tc.id)

        with st.expander(f"View steps — {tc.title}", expanded=False):
            if tc.steps:
                step_rows = []
                for i, s in enumerate(tc.steps, 1):
                    step_rows.append({
                        "#": i,
                        "Action": s.action,
                        "Selector": s.selector or "—",
                        "Value": s.value or "—",
                        "Force": "✓" if s.force else "",
                    })
                import pandas as pd
                st.dataframe(pd.DataFrame(step_rows), width='stretch', hide_index=True)
            st.markdown("**Test data:**")
            st.json(tc.test_data)

            # Phase 2: show variations if any
            if tc.variations:
                st.markdown(f"**Variations ({len(tc.variations)}):**")
                for v in tc.variations:
                    st.markdown(
                        f"- **{v.get('label', '?')}**: `{v.get('data', {})}` → "
                        f"{', '.join(v.get('expected_results', []))}"
                    )

            st.markdown("**Display script (not executed):**")
            st.code(tc.playwright_script, language="python")

    if not playwright_config:
        st.warning("Enter the App URL in the sidebar first.")
    else:
        col1, col2 = st.columns(2)
        with col1:
            run_btn = st.button(
                "▶️ Run Selected Tests",
                type="primary",
                width='stretch',
                disabled=len(selected_ids) == 0,
            )
        with col2:
            report_btn = st.button(
                "📄 Generate Report",
                width='stretch',
                disabled=len(st.session_state.executions) == 0,
            )

        if run_btn:
            to_run = [tc for tc in st.session_state.test_cases if tc.id in selected_ids]
            st.session_state.executions = []
            executor = SyncPlaywrightExecutor(playwright_config)

            # Build vision_fn if enabled (Phase 1)
            vision_fn = None
            if use_vision:
                def vision_fn(screenshot_path: str, expected_results: list):
                    return llm.analyze_screenshot(screenshot_path, expected_results)

            progress = st.progress(0)
            status = st.empty()

            # Phase 2 — decide whether to use variation runner or standard runner
            if generate_variations:
                total_items = sum(
                    max(1, len(tc.variations)) for tc in to_run
                )
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
            total_ran = len(st.session_state.executions)
            st.success(f"Done — {total_ran} executions completed.")

        if report_btn:
            with st.spinner("Generating report..."):
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
                    st.success("Report ready")
                except Exception as e:
                    st.error(str(e))

# ── Step 4: Results ────────────────────────────────────────────────────────
if st.session_state.executions:
    st.header("4️⃣ Results")
    m = get_metrics(st.session_state.executions)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total", m["total_executions"])
    c2.metric("Passed ✅", m["passed"])
    c3.metric("Failed ❌", m["failed"])
    c4.metric("Errors ⚠️", m["errors"])

    # Group executions: if variations present, group by test_case_id
    # Otherwise show flat list
    has_variations = any(
        getattr(ex, "variation_label", None) for ex in st.session_state.executions
    )

    if has_variations:
        # Phase 2: group by test_case_id
        from collections import defaultdict
        groups: dict = defaultdict(list)
        for ex in st.session_state.executions:
            groups[ex.test_case_id].append(ex)

        for tc_id, execs in groups.items():
            group_passed = sum(1 for e in execs if e.status == "passed")
            group_total = len(execs)
            group_icon = "✅" if group_passed == group_total else "⚠️" if group_passed > 0 else "❌"
            with st.expander(
                f"{group_icon} {tc_id} — {group_passed}/{group_total} passed",
                expanded=True,
            ):
                for ex in execs:
                    icon = "✅" if ex.status == "passed" else "❌" if ex.status == "failed" else "⚠️"
                    t = f"{ex.execution_time:.2f}s" if ex.execution_time is not None else "N/A"
                    var_label = f" [{ex.variation_label}]" if ex.variation_label else ""
                    retry_note = f" (attempt {ex.attempts})" if ex.attempts > 1 else ""
                    st.markdown(
                        f"{icon} **{ex.status.upper()}**{var_label}{retry_note} — {t}"
                    )
                    if ex.error_message:
                        if "Authentication failed" in ex.error_message:
                            st.warning(
                                f"**Auth failure:** {ex.error_message}\n\n"
                                "Check the Login URL, selectors, and credentials in the sidebar."
                            )
                        else:
                            st.error(ex.error_message)
                    # Phase 1: vision verdict
                    vv = getattr(ex, "vision_verdict", None)
                    if vv:
                        v_icon = "✅" if vv.get("passed") else "❌"
                        st.caption(
                            f"👁 Vision: {v_icon} "
                            f"(confidence {vv.get('confidence', 0):.0%}) — {vv.get('explanation', '')}"
                        )
                    if ex.screenshots:
                        cols = st.columns(min(3, len(ex.screenshots)))
                        for idx, s in enumerate(ex.screenshots):
                            if os.path.exists(s):
                                cols[idx % 3].image(s, width='stretch')
    else:
        # Standard flat list
        for ex in st.session_state.executions:
            icon = "✅" if ex.status == "passed" else "❌" if ex.status == "failed" else "⚠️"
            t = f"{ex.execution_time:.2f}s" if ex.execution_time is not None else "N/A"
            retry_note = f" · attempt {ex.attempts}" if ex.attempts > 1 else ""
            with st.expander(f"{icon} {ex.test_case_id} — {ex.status.upper()} ({t}){retry_note}"):
                if ex.error_message:
                    if "Authentication failed" in ex.error_message:
                        st.warning(
                            f"**Auth failure:** {ex.error_message}\n\n"
                            "Check the Login URL, username/password selectors, and credentials in the sidebar."
                        )
                    else:
                        st.error(ex.error_message)

                # Phase 1: vision verdict
                vv = getattr(ex, "vision_verdict", None)
                if vv:
                    v_icon = "✅" if vv.get("passed") else "❌"
                    conf = vv.get("confidence", 0)
                    st.info(
                        f"👁 Vision verification: {v_icon} "
                        f"(confidence {conf:.0%})\n\n{vv.get('explanation', '')}"
                    )

                if ex.screenshots:
                    cols = st.columns(min(3, len(ex.screenshots)))
                    for i, s in enumerate(ex.screenshots):
                        if os.path.exists(s):
                            cols[i % 3].image(s, width='stretch')

# ── Step 5: Report ─────────────────────────────────────────────────────────
if st.session_state.report:
    st.header("5️⃣ Report")
    report = st.session_state.report["data"]
    st.subheader("Summary")
    st.write(report.summary)
    m = report.metrics
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total", m["total_tests"])
    c2.metric("Passed", m["passed"])
    c3.metric("Failed", m["failed"])
    c4.metric("Pass Rate", f"{m['pass_rate']:.1f}%")
    st.subheader("Analysis")
    st.write(report.analysis)
    st.subheader("Recommendations")
    for r in report.recommendations:
        st.markdown(f"• {r}")
    st.download_button(
        "💾 Download Report",
        report.html_content,
        file_name=f"report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html",
        mime="text/html",
        width='stretch',
    )

st.markdown("---")
st.caption("Built with ❤️ by VG Platform")
