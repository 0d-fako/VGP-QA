"""Run New Test — a guided 6-step wizard.

Steps: 1 Target & Auth · 2 Requirements · 3 Advanced (optional) ·
4 Generate & Review · 5 Run · 6 Results & Report.
"""
import base64
import copy
import json
import uuid
from collections import defaultdict

import pandas as pd
import streamlit as st

from models import PlaywrightConfig, TestCase, TestStep
from services.playwright_executor import get_metrics, inspect_dom
from controllers.execution_controller import ExecutionController
from core.session_state import state
from ui.theme import mi, eyebrow, STATUS_MICON
from ui import components as C

STEP_LABELS = ["Target & Auth", "Requirements", "Advanced", "Generate & Review", "Run", "Results"]

_AUTH_LABELS = {
    "form":   "Form (username + password)",
    "cookie": "Cookie injection",
    "token":  "Bearer token",
}


# ── Wizard helpers ──────────────────────────────────────────────────────────

def _w(ctx) -> dict:
    """Wizard config dict, seeded from preferences on first use."""
    w = state.wizard
    if not w:
        p = ctx.prefs
        w.update({
            "raw_url": "", "base_url": "",
            "browser": p["browser"], "headless": p["headless"], "timeout": p["timeout"],
            "auth_type": "form", "use_auth": False, "credentials": None,
            "login_url": "", "username": "", "password": "",
            "u_sel": "#username", "p_sel": "#password", "s_sel": "",
            "shared_session": False, "generate_variations": False,
            "max_tc": 5,
        })
        state.wizard = w
    return w


def _reset_wizard() -> None:
    state.wizard = {}
    state.requirements = []
    state.test_cases = []
    state.executions = []
    state.report = None
    state.dom_snapshot = None
    state.design_context = None
    state.design_discrepancies = []
    state.clarifications = {}
    state.external_test_data = None
    state.custom_assertions = []
    state.db_run_id = None
    state.wizard_step = 1


def _nav_buttons(*, back=True, next_=True, next_disabled=False,
                 next_label="Next →", start_over=False) -> None:
    st.divider()
    c_back, c_mid, c_next = st.columns([1, 1, 2])
    with c_back:
        if back and st.button("← Back", use_container_width=True, key=f"wiz_back_{state.wizard_step}"):
            state.wizard_step = max(1, state.wizard_step - 1)
            st.rerun()
    with c_mid:
        if start_over and st.button("↺ Start over", use_container_width=True, key="wiz_startover"):
            _reset_wizard()
            st.rerun()
    with c_next:
        if next_ and st.button(next_label, type="primary", use_container_width=True,
                               disabled=next_disabled, key=f"wiz_next_{state.wizard_step}"):
            state.wizard_step = min(len(STEP_LABELS), state.wizard_step + 1)
            st.rerun()


# ── Step 1 — Target & Auth ──────────────────────────────────────────────────

def _step_target(ctx) -> None:
    w = _w(ctx)
    st.subheader("Target application")
    raw_url = st.text_input("App URL *", value=w.get("raw_url", ""),
                            placeholder="https://example.com", key="wiz_url")
    base_url = ""
    if raw_url:
        base_url = raw_url.strip()
        if not base_url.startswith(("http://", "https://")):
            base_url = "https://" + base_url
    w["raw_url"] = raw_url
    w["base_url"] = base_url

    if base_url:
        col_dom, col_ping = st.columns(2)
        with col_ping:
            if st.button(f"{mi('language')} Test URL",
                         help="Send a quick HEAD request to verify the URL is reachable"):
                with st.spinner("Checking…"):
                    try:
                        import requests as _requests
                        resp = _requests.head(base_url, timeout=8, allow_redirects=True)
                        if resp.status_code < 400:
                            st.success(f"Reachable ({resp.status_code})", icon=":material/check_circle:")
                        else:
                            st.warning(f"HTTP {resp.status_code} — site may require auth or redirect",
                                       icon=":material/warning:")
                    except Exception as ping_err:
                        st.error(f"Unreachable: {ping_err}", icon=":material/error:")
        with col_dom:
            if st.button(f"{mi('search')} Inspect DOM",
                         help="Navigate to the app and extract real CSS selectors"):
                with st.spinner("Inspecting live app DOM…"):
                    try:
                        snapshot = inspect_dom(
                            base_url=base_url, browser_type="chromium",
                            headless=True, timeout=30000,
                            credentials=w.get("credentials"),
                        )
                        state.dom_snapshot = snapshot
                        if snapshot.get("error"):
                            st.warning(f"Inspection warning: {snapshot['error']}")
                        else:
                            st.success(
                                f"Inspected: {snapshot.get('title', base_url)} "
                                f"({len(snapshot.get('inputs', []))} inputs, "
                                f"{len(snapshot.get('buttons', []))} buttons)"
                            )
                    except Exception as e:
                        st.error(f"DOM inspection failed: {e}")

        snap = state.dom_snapshot
        if snap and not snap.get("error"):
            with st.expander(f"{mi('content_paste')} DOM Snapshot", expanded=False):
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

    st.subheader("Browser")
    c1, c2, c3 = st.columns(3)
    browsers = ["chromium", "firefox", "webkit"]
    with c1:
        w["browser"] = st.selectbox("Engine", browsers,
                                    index=browsers.index(w.get("browser", "chromium"))
                                    if w.get("browser") in browsers else 0, key="wiz_browser")
    with c2:
        w["headless"] = st.checkbox("Headless", value=bool(w.get("headless", True)), key="wiz_headless")
    with c3:
        w["timeout"] = st.number_input("Timeout (ms)", value=int(w.get("timeout", 30000)),
                                       min_value=5000, max_value=120000, step=5000, key="wiz_timeout")
    st.caption("Defaults come from Preferences — adjust them here for this run.")

    st.subheader("Authentication")
    auth_types = ["form", "cookie", "token"]
    w["auth_type"] = st.selectbox(
        "Auth method", auth_types,
        index=auth_types.index(w.get("auth_type", "form")),
        format_func=lambda x: _AUTH_LABELS[x], key="wiz_auth_type",
    )
    w["use_auth"] = st.checkbox("Enable Auth", value=bool(w.get("use_auth", False)), key="wiz_use_auth")

    credentials = None
    if w["use_auth"]:
        at = w["auth_type"]
        if at == "form":
            w["login_url"] = st.text_input("Login URL", value=w.get("login_url", ""),
                                           placeholder="https://example.com/login", key="wiz_login_url")
            cc1, cc2 = st.columns(2)
            with cc1:
                w["u_sel"]    = st.text_input("Username selector", value=w.get("u_sel", "#username"), key="wiz_usel")
                w["username"] = st.text_input("Username", value=w.get("username", ""), key="wiz_user")
            with cc2:
                w["p_sel"]    = st.text_input("Password selector", value=w.get("p_sel", "#password"), key="wiz_psel")
                w["password"] = st.text_input("Password", type="password", value=w.get("password", ""), key="wiz_pass")
            w["s_sel"] = st.text_input("Submit selector (optional)", value=w.get("s_sel", ""),
                                       placeholder="button[type='submit']", key="wiz_ssel")
            if w["username"] and w["password"]:
                credentials = {
                    "login_url":         w.get("login_url", ""),
                    "username_selector": w["u_sel"],
                    "password_selector": w["p_sel"],
                    "submit_selector":   w["s_sel"],
                    "username":          w["username"],
                    "password":          w["password"],
                }
        elif at == "cookie":
            st.caption("Paste a JSON array of cookie objects.")
            cookie_json = st.text_area("Cookies (JSON)", height=100, value=w.get("cookie_json", ""), key="wiz_cookie")
            w["cookie_json"] = cookie_json
            if cookie_json.strip():
                credentials = {"cookies": cookie_json.strip()}
        elif at == "token":
            token_val = st.text_input("Bearer token", type="password", value=w.get("token", ""), key="wiz_token")
            w["token"] = token_val
            if token_val.strip():
                credentials = {"token": token_val.strip()}
    w["credentials"] = credentials
    state.wizard = w

    if not base_url:
        st.info("Enter an App URL to continue.", icon=":material/info:")
    _nav_buttons(back=False, next_disabled=not base_url, next_label="Next: Requirements →")


# ── Step 2 — Requirements ───────────────────────────────────────────────────

def _step_requirements(ctx) -> None:
    workflow = ctx.workflow
    st.subheader("Requirements")
    tab1, tab2, tab3 = st.tabs([f"{mi('upload_file')} Upload", f"{mi('edit_note')} Paste", f"{mi('bolt')} Sample"])

    with tab1:
        files = st.file_uploader(
            "Upload docs (PDF, DOCX, TXT, MD) — multiple allowed",
            type=["pdf", "docx", "txt", "md"], accept_multiple_files=True,
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
                    try:
                        state.requirements = workflow.analyze_requirements("\n\n".join(all_content))
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

    if state.requirements:
        col_req, col_amb = st.columns([3, 1])
        with col_amb:
            if st.button(f"{mi('search')} Check Ambiguity",
                         help="Score each requirement for clarity and testability"):
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
                f' <span class="ambiguous-badge">{C.icon("warning", 12)} Ambiguous ({score:.0%})</span>'
                if is_ambiguous else ""
            )
            with st.expander(f"**{req.title}** ({req.id}){badge}", expanded=is_ambiguous):
                st.write(req.description)
                for c in req.acceptance_criteria:
                    st.markdown(f"- {c}")
                if is_ambiguous:
                    if amb.get("issues"):
                        st.warning("**Issues found:** " + " · ".join(amb["issues"]))
                    if amb.get("suggestion"):
                        st.info(f"**Suggestion:** {amb['suggestion']}", icon=":material/lightbulb:")
                    existing = state.clarifications.get(req.id, "")
                    new_text = st.text_area(
                        "Add clarification (appended to requirement before generation):",
                        value=existing, key=f"clarify_{req.id}", height=80,
                    )
                    if new_text != existing:
                        state.clarifications[req.id] = new_text

    _nav_buttons(next_disabled=not state.requirements, next_label="Next: Advanced →")


# ── Step 3 — Advanced (optional) ────────────────────────────────────────────

def _step_advanced(ctx) -> None:
    workflow = ctx.workflow
    w = _w(ctx)
    st.subheader("Advanced options")
    st.caption("All optional — skip ahead if you don't need these.")

    # Coverage
    with st.container(border=True):
        st.markdown("**Coverage**")
        cov1, cov2 = st.columns(2)
        with cov1:
            w["generate_variations"] = st.checkbox(
                "Generate test variations", value=bool(w.get("generate_variations", False)),
                help="Ask Claude to generate 2-3 parameterized variations per test case.", key="wiz_variations")
        with cov2:
            w["shared_session"] = st.checkbox(
                "Shared browser session", value=bool(w.get("shared_session", False)),
                help="Authenticate once and share the browser context across all tests.", key="wiz_shared")
        state.wizard = w

    # Design assets
    with st.expander(f"{mi('palette')} Design Assets", expanded=False):
        st.caption(
            "Upload a screenshot or mockup of your app's UI. Claude analyses it against your "
            "requirements and injects visual validation steps into generated tests."
        )
        design_file = st.file_uploader("Upload design image (PNG, JPG, WEBP)",
                                       type=["png", "jpg", "jpeg", "webp"], key="design_upload")
        figma_url = st.text_input("Or paste a Figma/image URL", placeholder="https://…", key="figma_url")
        if st.button(f"{mi('search')} Analyse Design", key="analyse_design"):
            image_b64, media_type = None, "image/png"
            with st.spinner("Analysing design asset…"):
                try:
                    if design_file:
                        raw_bytes = design_file.read()
                        image_b64 = base64.standard_b64encode(raw_bytes).decode()
                        ext = design_file.name.rsplit(".", 1)[-1].lower()
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
            st.success("Design context ready — will be injected into next test generation.",
                       icon=":material/check_circle:")
            if st.button("Clear design context", key="clear_design"):
                state.design_context = None
                state.design_discrepancies = []

    # External test data
    with st.expander(f"{mi('folder_open')} External Test Data", expanded=False):
        st.caption("Upload a CSV or JSON file with test data rows. Each row becomes a variation.")
        td_file = st.file_uploader("Upload CSV or JSON", type=["csv", "json"], key="testdata_upload")
        if td_file:
            try:
                if td_file.name.endswith(".csv"):
                    state.external_test_data = pd.read_csv(td_file).to_dict(orient="records")
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
        if state.external_test_data and st.button("Clear test data", key="clear_td"):
            state.external_test_data = None

    # Custom assertions
    with st.expander(f"{mi('edit')} Custom Assertion Rules", expanded=False):
        st.caption(
            "Add custom text or attribute assertions. Injected as `check_text` / "
            "`check_attribute` steps into every generated test case."
        )
        new_rule_text = st.text_input("Assertion rule",
                                      placeholder='e.g. "Welcome" or "aria-label=Submit"',
                                      key="custom_assert_input")
        if st.button(f"{mi('add')} Add Rule", key="add_custom_assert"):
            rule = new_rule_text.strip()
            if rule and rule not in state.custom_assertions:
                state.custom_assertions.append(rule)
        if state.custom_assertions:
            st.markdown("**Active rules:**")
            to_remove = []
            for i, rule in enumerate(state.custom_assertions):
                c1, c2 = st.columns([5, 1])
                c1.code(rule, language=None)
                if c2.button(mi("close"), key=f"remove_rule_{i}"):
                    to_remove.append(rule)
            for r in to_remove:
                state.custom_assertions.remove(r)

    _nav_buttons(next_label="Next: Generate →")


# ── Step 4 — Generate & Review ──────────────────────────────────────────────

def _step_generate(ctx) -> None:
    workflow = ctx.workflow
    w = _w(ctx)
    u_sel, p_sel, s_sel = w.get("u_sel", "#username"), w.get("p_sel", "#password"), w.get("s_sel", "")

    st.subheader("Generate test cases")
    w["max_tc"] = st.slider("Max test cases", 1, 10, int(w.get("max_tc", 5)), key="wiz_max_tc")
    state.wizard = w

    snap = state.dom_snapshot
    if snap and not snap.get("error"):
        st.info(
            f"DOM snapshot available ({len(snap.get('inputs', []))} inputs, "
            f"{len(snap.get('buttons', []))} buttons) — real selectors will be injected."
        )
    elif w.get("base_url"):
        st.caption(f"{mi('lightbulb')} Tip: use **Inspect DOM** in Step 1 to inject real selectors.")
    if state.design_context:
        st.info("Design context will be injected into test generation.", icon=":material/palette:")
    if state.custom_assertions:
        st.info(f"{len(state.custom_assertions)} custom assertion rule(s) will be appended to every test.",
                icon=":material/edit:")
    if state.external_test_data:
        st.info(f"{len(state.external_test_data)} external data row(s) loaded — will be used as variations.",
                icon=":material/folder_open:")

    limit_hit = workflow.rate_limit_exceeded()
    if limit_hit:
        st.error("Session API call limit reached. Refresh the page to generate more test cases.",
                 icon=":material/block:")
    if st.button(f"{mi('science')} Generate Test Cases", type="primary",
                 use_container_width=True, disabled=limit_hit):
        if not state.generating:
            state.generating = True
            with st.spinner("Generating…"):
                try:
                    reqs_for_gen = workflow.apply_clarifications(state.requirements, state.clarifications)
                    tcs = workflow.generate_test_cases(
                        reqs_for_gen, username_selector=u_sel, password_selector=p_sel,
                        submit_selector=s_sel, max_cases=w["max_tc"], dom_snapshot=state.dom_snapshot,
                        generate_variations=w.get("generate_variations", False),
                        design_context=state.design_context,
                    )
                    if state.external_test_data:
                        workflow.inject_external_data(tcs, state.external_test_data)
                    if state.custom_assertions:
                        workflow.inject_custom_assertions(tcs, state.custom_assertions)
                    state.test_cases = tcs
                    st.session_state.selected_tests = [tc.id for tc in tcs]
                    variation_total = sum(len(tc.variations) for tc in tcs)
                    if variation_total:
                        st.success(f"Generated {len(tcs)} test cases ({variation_total} variations across all tests)")
                    else:
                        st.success(f"Generated {len(tcs)} test cases")
                except Exception as e:
                    st.error(str(e))
                finally:
                    state.generating = False

    if state.test_cases:
        st.divider()
        _render_management(ctx, w)

    n_approved = sum(1 for tc in state.test_cases if tc.approved)
    if state.test_cases and n_approved == 0:
        st.caption("Approve at least one test to continue.")
    _nav_buttons(next_disabled=n_approved == 0, next_label="Next: Run →")


def _render_management(ctx, w) -> None:
    workflow = ctx.workflow
    u_sel, p_sel, s_sel = w.get("u_sel", "#username"), w.get("p_sel", "#password"), w.get("s_sel", "")
    editor_cfg = C.editor_config()

    st.subheader("Review & approve")

    all_suites = sorted({tc.suite or "Unsorted" for tc in state.test_cases})
    tb1, tb2, tb3, tb4 = st.columns([2, 3, 2, 2])
    with tb1:
        suite_filter = st.selectbox(f"{mi('folder')} Suite", ["All"] + all_suites, key="suite_filter")
    with tb2:
        bulk_action = st.selectbox(
            "Bulk action",
            ["— select —", "Approve selected", "Regenerate selected",
             "Add step to selected", "Delete selected"], key="bulk_action",
        )
    with tb3:
        bulk_apply = st.button(f"{mi('play_arrow')} Apply", key="bulk_apply_btn", use_container_width=True)
    with tb4:
        if st.button(f"{mi('add')} New test", key="new_test_btn", use_container_width=True):
            state.show_create_form = not state.show_create_form

    # Create-test form
    if state.show_create_form:
        with st.container(border=True):
            st.subheader(f"{mi('edit')} Create test manually")
            cf1, cf2 = st.columns(2)
            with cf1:
                new_title = st.text_input("Title *", key="new_tc_title", placeholder="e.g. Verify login button")
                new_suite = st.text_input("Suite/folder", key="new_tc_suite", placeholder="e.g. Authentication")
            with cf2:
                req_labels = ([f"{r.id} — {r.title}" for r in state.requirements]
                              if state.requirements else ["(none)"])
                new_req_sel  = st.selectbox("Requirement", req_labels, key="new_tc_req")
                new_expected = st.text_area("Expected results (one per line)", key="new_tc_expected", height=80)

            st.markdown("**Steps** *(add rows below)*")
            _empty_df = pd.DataFrame(columns=["#", "Type", "Action", "Selector", "Value", "Force"])
            new_steps_df = st.data_editor(_empty_df, key="new_tc_steps_editor",
                                          num_rows="dynamic", hide_index=True, column_config=editor_cfg)
            cfa, cfb = st.columns(2)
            with cfa:
                if st.button(f"{mi('save')} Save test", key="save_new_tc", type="primary"):
                    if not new_title.strip():
                        st.error("Title is required.")
                    else:
                        req_id = ""
                        if state.requirements and "(none)" not in req_labels:
                            req_id = new_req_sel.split(" — ")[0]
                        steps  = C.df_to_steps(new_steps_df) if not new_steps_df.empty else []
                        expect = [ln.strip() for ln in new_expected.strip().splitlines() if ln.strip()]
                        tc_id  = f"TC-{uuid.uuid4().hex[:8].upper()}"
                        state.test_cases.append(TestCase(
                            id=tc_id, requirement_id=req_id, title=new_title.strip(),
                            steps=steps, test_data={}, expected_results=expect,
                            suite=new_suite.strip() or None, approved=False,
                        ))
                        state.show_create_form = False
                        st.success(f"Created {tc_id} — {new_title.strip()}")
                        st.rerun()
            with cfb:
                if st.button(f"{mi('close')} Cancel", key="cancel_new_tc"):
                    state.show_create_form = False
                    st.rerun()

    # Bulk add-step form
    if state.show_bulk_step_form:
        with st.container(border=True):
            st.subheader(f"{mi('add')} Add step to selected tests")
            bs1, bs2, bs3 = st.columns([2, 2, 2])
            with bs1:
                bs_action = st.selectbox("Action", C.ALL_ACTIONS, key="bulk_step_action")
            with bs2:
                bs_sel = st.text_input("Selector", key="bulk_step_sel")
            with bs3:
                bs_val = st.text_input("Value", key="bulk_step_val")
            bpos_col, bconf_col, bcanc_col = st.columns(3)
            bs_pos = bpos_col.radio("Insert at", ["End", "Beginning"], key="bulk_step_pos", horizontal=True)
            if bconf_col.button("Apply to selected", key="bulk_step_confirm"):
                targets = {tc.id for tc in state.test_cases if st.session_state.get(f"chk_{tc.id}", True)}
                new_step = TestStep(action=bs_action, selector=bs_sel.strip() or None, value=bs_val.strip() or None)
                for tc in state.test_cases:
                    if tc.id in targets:
                        tc.steps.insert(0, new_step) if bs_pos == "Beginning" else tc.steps.append(new_step)
                        tc.approved = False
                state.show_bulk_step_form = False
                st.success(f"Step added to {len(targets)} test(s). Approval reset.")
                st.rerun()
            if bcanc_col.button(f"{mi('close')} Cancel", key="bulk_step_cancel"):
                state.show_bulk_step_form = False
                st.rerun()

    filtered_tcs = [tc for tc in state.test_cases
                    if suite_filter == "All" or (tc.suite or "Unsorted") == suite_filter]

    if filtered_tcs:
        sa_col, _ = st.columns([1, 11])
        if sa_col.checkbox("All", key="select_all_chk"):
            for tc in filtered_tcs:
                st.session_state[f"chk_{tc.id}"] = True

    # Bulk apply
    if bulk_apply and bulk_action != "— select —":
        selected_now = {tc.id for tc in filtered_tcs if st.session_state.get(f"chk_{tc.id}", True)}
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
            with st.status(f"Regenerating {len(selected_now)} test(s)…", expanded=True) as _rstat:
                for tc in list(state.test_cases):
                    if tc.id not in selected_now:
                        continue
                    st.write(f"{mi('hourglass_empty')} {tc.title}…")
                    new_tc, err = workflow.regenerate_one(
                        tc, state.requirements, username_selector=u_sel, password_selector=p_sel,
                        submit_selector=s_sel, dom_snapshot=state.dom_snapshot, design_context=state.design_context)
                    if new_tc:
                        idx = next(i for i, t in enumerate(state.test_cases) if t.id == tc.id)
                        state.test_cases[idx] = new_tc
                        st.write(f"{mi('check_circle')} {tc.title}")
                    else:
                        st.write(f"{mi('cancel')} {tc.title}: {err}")
                _rstat.update(label="Bulk regeneration complete", state="complete", expanded=False)
            st.rerun()
        elif "Add step" in bulk_action:
            state.show_bulk_step_form = True
            st.rerun()

    # Grouped render
    suite_groups: dict = defaultdict(list)
    for tc in filtered_tcs:
        suite_groups[tc.suite or "Unsorted"].append(tc)
    sorted_keys = sorted(k for k in suite_groups if k != "Unsorted")
    if "Unsorted" in suite_groups:
        sorted_keys.append("Unsorted")

    for s_name in sorted_keys:
        s_tcs = suite_groups[s_name]
        n_approved = sum(1 for t in s_tcs if t.approved)
        if len(sorted_keys) > 1 or s_name != "Unsorted":
            st.markdown(
                f'{C.icon("folder", 15)} <b>{s_name}</b> &nbsp;&nbsp;'
                f'<span class="badge badge-pass">{n_approved} approved</span> '
                f'<span class="badge badge-retry">{len(s_tcs) - n_approved} pending</span>',
                unsafe_allow_html=True,
            )
        for tc in s_tcs:
            (c_chk, c_status, c_title, c_suite, c_approve, c_dup, c_del) = st.columns(
                [0.5, 1, 4, 1.5, 1.2, 0.8, 0.8])
            with c_chk:
                st.checkbox("", value=st.session_state.get(f"chk_{tc.id}", True),
                            key=f"chk_{tc.id}", label_visibility="collapsed")
            with c_status:
                if tc.approved:
                    st.markdown(f'<span class="badge badge-pass">{C.icon("check_circle", 13)} approved</span>',
                                unsafe_allow_html=True)
                else:
                    st.markdown(f'<span class="badge badge-error">{C.icon("hourglass_empty", 13)} pending</span>',
                                unsafe_allow_html=True)
            with c_title:
                st.markdown(f"**{tc.title}** `{tc.id}`")
            with c_suite:
                new_suite_val = st.text_input("Suite", value=tc.suite or "", key=f"suite_input_{tc.id}",
                                              label_visibility="collapsed", placeholder="Suite…")
                if new_suite_val.strip() != (tc.suite or ""):
                    tc.suite = new_suite_val.strip() or None
            with c_approve:
                if not tc.approved:
                    if st.button(f"{mi('check_circle')} Approve", key=f"approve_{tc.id}"):
                        tc.approved = True
                        st.rerun()
                else:
                    if st.button(f"{mi('undo')} Revoke", key=f"revoke_{tc.id}"):
                        tc.approved = False
                        st.rerun()
            with c_dup:
                if st.button(mi("content_copy"), key=f"dup_{tc.id}", help="Duplicate"):
                    dup = copy.deepcopy(tc)
                    dup.id = f"TC-{uuid.uuid4().hex[:8].upper()}"
                    dup.title = f"{tc.title} (copy)"
                    dup.approved = False
                    idx = next(i for i, t in enumerate(state.test_cases) if t.id == tc.id)
                    state.test_cases.insert(idx + 1, dup)
                    st.rerun()
            with c_del:
                if st.button(mi("delete"), key=f"del_{tc.id}", help="Delete"):
                    state.test_cases = [t for t in state.test_cases if t.id != tc.id]
                    st.rerun()

            with st.expander(f"{mi('edit')} {tc.title}", expanded=False):
                edited = st.data_editor(C.steps_to_df(tc), key=f"editor_{tc.id}",
                                        num_rows="dynamic", hide_index=True, column_config=editor_cfg)
                ep1, ep2 = st.columns(2)
                with ep1:
                    if st.button(f"{mi('save')} Apply edits", key=f"apply_{tc.id}"):
                        new_steps = C.df_to_steps(edited)
                        if new_steps:
                            tc.steps = new_steps
                            tc.approved = False
                            st.success(f"{len(new_steps)} step(s) saved — approval reset.")
                        else:
                            st.warning("No valid steps.")
                with ep2:
                    if st.button(f"{mi('refresh')} Regenerate", key=f"regen_{tc.id}"):
                        with st.spinner("Regenerating…"):
                            new_tc, err = workflow.regenerate_one(
                                tc, state.requirements, username_selector=u_sel, password_selector=p_sel,
                                submit_selector=s_sel, dom_snapshot=state.dom_snapshot,
                                design_context=state.design_context)
                        if new_tc:
                            idx = next(i for i, t in enumerate(state.test_cases) if t.id == tc.id)
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
                        st.markdown(f"- **{v.get('label','?')}**: `{v.get('data',{})}` → "
                                    f"{', '.join(v.get('expected_results',[]))}")
                st.markdown("**Display script:**")
                st.code(tc.playwright_script, language="python")

        if s_name != sorted_keys[-1]:
            st.divider()


# ── Step 5 — Run ────────────────────────────────────────────────────────────

def _step_run(ctx) -> None:
    w = _w(ctx)
    workflow, storage, db = ctx.workflow, ctx.storage, ctx.db
    prefs = ctx.prefs

    approved = [tc for tc in state.test_cases if tc.approved]
    st.subheader("Run approved tests")

    if not w.get("base_url"):
        st.warning("No App URL set — go back to Step 1.", icon=":material/warning:")
        _nav_buttons(next_=False)
        return
    if not approved:
        st.warning("No approved tests — go back to Step 4 and approve some.", icon=":material/warning:")
        _nav_buttons(next_=False)
        return

    st.markdown(
        f"Ready to run **{len(approved)} approved test(s)** against "
        f"`{w['base_url']}` using **{w['browser']}** "
        f"({'headless' if w['headless'] else 'headed'})."
    )

    if st.button(f"{mi('play_arrow')} Run {len(approved)} test(s)", type="primary", use_container_width=True):
        cfg = PlaywrightConfig(
            base_url=w["base_url"], browser=w["browser"], headless=bool(w["headless"]),
            timeout=int(w["timeout"]), credentials=w.get("credentials"),
            max_retries=int(prefs["max_retries"]), auth_type=w.get("auth_type", "form"),
            shared_session=bool(w.get("shared_session", False)),
            per_step_screenshots=bool(prefs["per_step_screenshots"]),
        )
        state.executions = []
        executor = ExecutionController(cfg, storage, db)

        vision_fn = None
        if prefs["use_vision"]:
            def vision_fn(screenshot_path: str, expected_results: list):
                return workflow.analyze_screenshot(screenshot_path, expected_results)

        use_variations = w.get("generate_variations", False) or any(tc.variations for tc in approved)
        total_items = (sum(max(1, len(tc.variations)) for tc in approved)
                       if use_variations else len(approved))
        completed, passed_so_far = 0, 0

        with st.status(f"{mi('rocket_launch')} Running tests…", expanded=True) as run_status:
            prog = st.progress(0.0)
            for tc, result in executor.iter_run(approved, use_variations=use_variations, vision_fn=vision_fn):
                state.executions.append(result)
                completed += 1
                if result.status == "passed":
                    passed_so_far += 1
                icon_tok = mi(STATUS_MICON.get(result.status, "help"))
                vlab = f" [{result.variation_label}]" if getattr(result, "variation_label", None) else ""
                t = f"{result.execution_time:.1f}s" if result.execution_time else ""
                st.write(f"{icon_tok} {tc.title}{vlab}  {t}")
                prog.progress(completed / total_items)

            failed_count = completed - passed_so_far
            label = (f"{mi('check_circle')} All {completed} tests passed" if failed_count == 0
                     else f"Done — {passed_so_far}/{completed} passed, {failed_count} failed")
            run_status.update(label=label, state="complete" if failed_count == 0 else "error", expanded=False)

        st.success(f"Completed {len(state.executions)} execution(s).")
        if db:
            try:
                run_name = ExecutionController.default_run_name(w["base_url"])
                new_run_id = executor.save_run(run_name, state.requirements, state.test_cases,
                                               state.executions, existing_run_id=state.db_run_id)
                if new_run_id:
                    state.db_run_id = new_run_id
                st.info("Results saved to database.", icon=":material/save:")
            except Exception as db_err:
                st.warning(f"DB save failed: {db_err}")

    if state.executions:
        st.caption(f"{len(state.executions)} result(s) ready — continue to view them.")
    _nav_buttons(next_disabled=not state.executions, next_label="Next: Results →")


# ── Step 6 — Results & Report ───────────────────────────────────────────────

def _step_results(ctx) -> None:
    report_svc = ctx.report_svc
    st.subheader("Results")

    if not state.executions:
        st.info("No results yet — go back to Step 5 and run your approved tests.", icon=":material/info:")
        _nav_buttons(next_=False, start_over=True)
        return

    execs = state.executions
    m = get_metrics(execs)
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Total",     m["total_executions"])
    k2.metric("Passed",    m["passed"])
    k3.metric("Failed",    m["failed"])
    k4.metric("Errors",    m["errors"])
    k5.metric("Pass Rate", f"{m['pass_rate']:.1f}%")

    has_times = any(e.execution_time for e in execs)
    err_breakdown = C.session_error_breakdown(execs)
    chart_cols = st.columns(2 if err_breakdown else 1)
    with chart_cols[0]:
        if has_times:
            st.caption("**Execution time per test**")
            time_data = pd.DataFrame({
                "Test":         [e.test_case_id[:18] for e in execs],
                "Duration (s)": [round(e.execution_time or 0, 2) for e in execs],
            }).set_index("Test")
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
            + ", ".join(f"`{tc_map.get(e.test_case_id, e.test_case_id)}`" for e in flaky_in_session)
        )

    st.divider()
    tc_title_map = {tc.id: tc.title for tc in state.test_cases}
    has_variations = any(getattr(ex, "variation_label", None) for ex in execs)
    if has_variations:
        groups: dict = defaultdict(list)
        for ex in execs:
            groups[ex.test_case_id].append(ex)
        for tc_id, group_execs in groups.items():
            g_pass  = sum(1 for e in group_execs if e.status == "passed")
            g_total = len(group_execs)
            g_icon  = (mi("check_circle") if g_pass == g_total
                       else mi("warning") if g_pass > 0 else mi("cancel"))
            with st.expander(f"{g_icon} {tc_title_map.get(tc_id, tc_id)} — {g_pass}/{g_total} passed", expanded=True):
                for ex in group_execs:
                    C.render_result_card(ex, tc_title=tc_title_map.get(ex.test_case_id, ""))
    else:
        for ex in execs:
            C.render_result_card(ex, tc_title=tc_title_map.get(ex.test_case_id, ""))

    # ── Report ──────────────────────────────────────────────────────────────
    st.divider()
    st.subheader("Report")
    if st.button(f"{mi('description')} Generate Report", type="primary"):
        with st.spinner("Generating report…"):
            try:
                report = report_svc.generate(state.executions, state.requirements)
                url = report_svc.upload(report)
                state.report = {"data": report, "url": url}
                if state.db_run_id:
                    try:
                        report_svc.save_to_db(state.db_run_id, report)
                    except Exception as db_err:
                        st.warning(f"DB report save failed: {db_err}", icon=":material/warning:")
                st.success("Report ready")
            except Exception as e:
                st.error(str(e))

    if state.report:
        report = state.report["data"]
        ts = pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')
        report_tabs = st.tabs([f"{mi('description')} Summary", f"{mi('map')} Traceability Matrix"])
        with report_tabs[0]:
            st.markdown("**Summary**")
            st.write(report.summary)
            rm = report.metrics
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Total",     rm["total_tests"])
            c2.metric("Passed",    rm["passed"])
            c3.metric("Failed",    rm["failed"])
            c4.metric("Pass Rate", f"{rm['pass_rate']:.1f}%")
            st.markdown("**Analysis**")
            st.write(report.analysis)
            st.markdown("**Recommendations**")
            for r in report.recommendations:
                st.markdown(f"• {r}")
            dl1, dl2, dl3 = st.columns(3)
            with dl1:
                st.download_button(f"{mi('download')} Download HTML", report.html_content,
                                   file_name=f"report_{ts}.html", mime="text/html", use_container_width=True)
            with dl2:
                st.download_button(f"{mi('download')} Download CSV",
                                   report_svc.export_csv(state.executions, state.test_cases),
                                   file_name=f"report_{ts}.csv", mime="text/csv", use_container_width=True)
            with dl3:
                st.download_button(f"{mi('download')} Download JUnit XML",
                                   report_svc.export_junit(state.executions, state.test_cases),
                                   file_name=f"report_{ts}.xml", mime="application/xml", use_container_width=True)
        with report_tabs[1]:
            st.markdown("**Requirements Traceability Matrix**")
            st.caption("Maps each requirement → its test cases → execution results")
            df_matrix = report_svc.build_traceability_matrix(
                state.requirements, state.test_cases, state.executions)
            st.dataframe(df_matrix, hide_index=True, use_container_width=True)
            st.download_button(f"{mi('download')} Download Traceability CSV",
                               df_matrix.to_csv(index=False),
                               file_name=f"traceability_{ts}.csv", mime="text/csv")

    _nav_buttons(next_=False, start_over=True)


# ── Entry ───────────────────────────────────────────────────────────────────

_STEPS = {1: _step_target, 2: _step_requirements, 3: _step_advanced,
          4: _step_generate, 5: _step_run, 6: _step_results}


def render(ctx) -> None:
    eyebrow("Run New Test")
    st.title("Run New Test")
    C.stepper(STEP_LABELS, state.wizard_step)
    _STEPS.get(state.wizard_step, _step_target)(ctx)
