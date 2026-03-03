import json
import re
import base64
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional

from anthropic import Anthropic
from config import config
from models import Requirement, TestCase, TestStep, TestReport

logger = logging.getLogger(__name__)


MODELS = [
    "claude-sonnet-4-6",
    "claude-3-5-haiku-20241022",
    "claude-3-haiku-20240307",
]

# Actions the executor accepts.  Used here to validate LLM output.
ALLOWED_ACTIONS = {
    # ── Navigation & interaction ──────────────────────────────────────────
    "goto", "fill", "click", "check", "press",
    "wait_for_selector", "wait_for_load_state", "wait_for_timeout",
    "scroll_to",       # Scroll element into viewport
    "hover",           # Hover over element (dropdowns, tooltips)
    "select",          # Choose option from <select> by value or label
    "click_text",      # Click button/link by visible text — NO CSS selector needed
    # ── Assertions (Phase 3) ─────────────────────────────────────────────
    "check_url",       # Assert URL contains (or !not) a path fragment
    "check_text",      # Assert page/element text contains (or !not) a string
    "check_element",   # Assert element state: visible|hidden|enabled|disabled|checked|unchecked
    "check_attribute", # Assert element attribute: "attr=expected_value"
    "check_count",     # Assert count of matching elements equals an integer
}


# ── Internal helpers ────────────────────────────────────────────────────────

def _call_claude(client: Anthropic, prompt: str, model_ref: list, max_tokens: int = 4096) -> str:
    """Try models in order; return text from the first that responds."""
    for model in MODELS:
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            model_ref[0] = model
            logger.info("Model used: %s", model)
            return resp.content[0].text
        except Exception as e:
            if "not_found_error" in str(e) or "404" in str(e):
                logger.warning("%s unavailable, trying next...", model)
                continue
            raise
    raise RuntimeError("No available Claude model found.")


def _parse_json(text: str) -> dict:
    """
    Robustly extract a JSON object from an LLM response.

    Strategy (in order):
      1. Strip markdown code fences and parse directly.
      2. Search for the outermost {...} block with re.DOTALL.
      3. Manual brace-counter that skips quoted strings.
    """
    # Strip ``` fences
    text = re.sub(r"^```(?:json)?\s*", "", text.strip())
    text = re.sub(r"\s*```$", "", text)

    # 1. Direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 2. Regex find outermost object
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass

    # 3. Manual brace-counter (skips chars inside quoted strings)
    depth = 0
    start = None
    in_string = False
    escape_next = False
    for i, ch in enumerate(text):
        if escape_next:
            escape_next = False
            continue
        if ch == "\\" and in_string:
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            if start is None:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    start = None

    raise ValueError(f"No valid JSON object found. Preview: {text[:300]}")


def _steps_to_script(steps: List[dict], test_data: dict) -> str:
    """
    Render a list of step dicts as a human-readable pseudo-script.
    """
    lines = ["# Auto-generated display script (not executed)"]
    for s in steps:
        action = s.get("action", "?")
        selector = s.get("selector", "")
        value = s.get("value", "")
        if action == "goto":
            url = value.replace("{{url}}", test_data.get("url", "<base_url>"))
            lines.append(f"await page.goto({url!r})")
        elif action == "fill":
            resolved = value
            for k, v in test_data.items():
                resolved = resolved.replace(f"{{{{{k}}}}}", str(v))
            lines.append(f"await page.fill({selector!r}, {resolved!r})")
        elif action == "click":
            force_note = "  # force=True" if s.get("force") else ""
            lines.append(f"await page.click({selector!r}){force_note}")
        elif action == "check":
            lines.append(f"await page.check({selector!r})  # checkbox/radio")
        elif action == "press":
            lines.append(f"await page.press({selector!r}, {value!r})")
        elif action == "wait_for_selector":
            lines.append(f"await page.wait_for_selector({selector!r})")
        elif action == "wait_for_load_state":
            lines.append(f"await page.wait_for_load_state({value!r})")
        elif action == "wait_for_timeout":
            lines.append(f"await page.wait_for_timeout({value})")
        elif action == "check_url":
            if value and value.startswith("!"):
                lines.append(f"assert {value[1:]!r} not in page.url  # URL NOT present check")
            else:
                lines.append(f"assert {value!r} in page.url  # URL verification")
        # ── Phase 3 ──────────────────────────────────────────────────────
        elif action == "check_text":
            negate = value.startswith("!") if value else False
            text_val = value[1:] if negate else value
            verb = "not in" if negate else "in"
            target = selector or "body"
            lines.append(f"assert {text_val!r} {verb} page.locator({target!r}).text_content()  # text check")
        elif action == "check_element":
            lines.append(f"# assert page.locator({selector!r}).{value}()  # element state check")
        elif action == "check_attribute":
            attr, _, expected = (value or "").partition("=")
            lines.append(f"assert page.get_attribute({selector!r}, {attr.strip()!r}) == {expected.strip()!r}  # attribute check")
        elif action == "check_count":
            lines.append(f"assert page.locator({selector!r}).count() == {value}  # count check")
        elif action == "scroll_to":
            lines.append(f"await page.locator({selector!r}).scroll_into_view_if_needed()")
        elif action == "hover":
            lines.append(f"await page.hover({selector!r})")
        elif action == "select":
            lines.append(f"await page.select_option({selector!r}, value={value!r})")
        elif action == "click_text":
            lines.append(f"await page.get_by_role('button', name={value!r}).click()  # text-based click")
        else:
            lines.append(f"# unknown action: {action}")
    return "\n".join(lines)


def _validate_steps(steps: list, title: str) -> List[TestStep]:
    """
    Validate each step dict from the LLM response.

    Rules enforced here:
    - Steps must be a non-empty list of dicts.
    - Each action must be in ALLOWED_ACTIONS.
    - Actions that need a selector (fill/click/press/wait_for_selector) must have one.
    - Any selector containing 'data-testid' is stripped and warned about:
      the LLM invents these attributes; they almost never exist in real apps.
      The check_url action should be used for post-navigation verification instead.

    Raises ValueError only for structural errors (wrong type, unknown action, missing
    selector for interactive steps). Returns only the valid, safe steps.
    """
    if not isinstance(steps, list) or not steps:
        raise ValueError(f"'{title}': steps must be a non-empty list")

    result = []
    for i, s in enumerate(steps):
        if not isinstance(s, dict):
            raise ValueError(f"'{title}' step[{i}] is not a dict")

        action = s.get("action", "").strip()
        if action not in ALLOWED_ACTIONS:
            raise ValueError(
                f"'{title}' step[{i}] has unknown action '{action}'. "
                f"Allowed: {sorted(ALLOWED_ACTIONS)}"
            )

        selector = s.get("selector", "") or ""

        # Strip steps that use data-testid selectors — the LLM hallucinates these
        # attribute values.  They time out every time because the real app doesn't
        # use them.  Warn so the user can see what was dropped.
        if "data-testid" in selector:
            logger.warning(
                "'%s' step[%d]: dropping data-testid selector '%s'. "
                "Use check_url for post-navigation verification instead.",
                title, i, selector,
            )
            continue

        # Actions that require a selector — validated here so the executor never
        # receives a step that would immediately raise an AttributeError.
        SELECTOR_REQUIRED = {
            "fill", "click", "check", "press", "wait_for_selector",
            # Phase 3 interaction
            "scroll_to", "hover", "select",
            # Phase 3 assertions (check_text is optional — defaults to body)
            "check_element", "check_attribute", "check_count",
            # click_text is NOT here — it uses value (text), not selector
        }
        if action in SELECTOR_REQUIRED and not selector:
            raise ValueError(f"'{title}' step[{i}] action '{action}' requires a selector")

        # click_text requires a value (the text to find) — selector is not used
        raw_val = s.get("value") or ""
        if action == "click_text" and not raw_val.strip():
            raise ValueError(f"'{title}' step[{i}] action 'click_text' requires a value (the text to click)")

        # check_attribute: value must contain "=" to split attribute name from expected value
        if action == "check_attribute" and "=" not in raw_val:
            logger.warning(
                "'%s' step[%d]: check_attribute value '%s' missing '='. "
                "Format must be 'attribute=expected_value'. Dropping step.",
                title, i, raw_val,
            )
            continue

        result.append(TestStep(
            action=action,
            selector=selector or None,
            value=raw_val or None,
            timeout=s.get("timeout"),
            force=bool(s.get("force", False)),
        ))

    if not result:
        raise ValueError(f"'{title}': all steps were invalid or filtered — nothing to execute")

    return result


def _format_dom_snapshot(dom_data: Optional[Dict]) -> str:
    """
    Format the DOM inspection result as a concise prompt snippet.
    Injects real CSS selectors into the LLM prompt so generated steps
    target elements that actually exist in the application (Phase 1).
    Returns an empty string when no valid snapshot is available.
    """
    if not dom_data or dom_data.get("error"):
        return ""

    lines = [
        f"LIVE APP DOM SNAPSHOT",
        f"  URL   : {dom_data.get('url', 'unknown')}",
        f"  Title : {dom_data.get('title', '')}",
    ]

    inputs = dom_data.get("inputs", [])
    if inputs:
        lines.append("\nINPUT FIELDS — use EXACTLY these selectors:")
        for inp in inputs[:12]:
            label = f"  [{inp['label']}]" if inp.get("label") else ""
            ph = f"  placeholder='{inp['placeholder']}'" if inp.get("placeholder") else ""
            lines.append(f"  {inp['selector']}  (type={inp.get('type', 'text')}{ph}{label})")

    buttons = dom_data.get("buttons", [])
    if buttons:
        lines.append("\nBUTTONS & LINKS — use EXACTLY these selectors:")
        for btn in buttons[:10]:
            text = f'  "{btn["text"]}"' if btn.get("text") else ""
            lines.append(f"  {btn['selector']}{text}")

    forms = dom_data.get("forms", [])
    if forms:
        lines.append("\nFORMS:")
        for form in forms[:3]:
            fid = f" id={form['id']}" if form.get("id") else ""
            fields = ", ".join(form.get("fields", [])[:6])
            lines.append(f"  <form{fid} method={form.get('method','get')}> — fields: {fields}")

    headings = dom_data.get("headings", [])
    if headings:
        lines.append(f"\nPAGE HEADINGS: {', '.join(headings[:4])}")

    lines.append(
        "\n⚠️  These selectors are from the LIVE application. "
        "Prefer them over any guessed values."
    )

    return "\n".join(lines)


# ── LLMProcessor ────────────────────────────────────────────────────────────

class LLMProcessor:
    def __init__(self):
        config.validate()
        self.client = Anthropic(api_key=config.CLAUDE_API_KEY)
        self._model = [MODELS[0]]

    # ── Requirements analysis ──────────────────────────────────────────────

    def analyze_requirements(self, document_content: str) -> List[Requirement]:
        logger.info("Analyzing requirements document...")
        prompt = f"""Analyze this requirement document and extract testable requirements.

DOCUMENT:
{document_content}

Return ONLY a raw JSON object — no markdown, no explanation, no code fences.

{{
    "requirements": [
        {{
            "title": "Short title",
            "description": "What this requires",
            "acceptance_criteria": ["criterion 1", "criterion 2"]
        }}
    ]
}}"""
        text = _call_claude(self.client, prompt, self._model)
        data = _parse_json(text)
        reqs = [
            Requirement(
                id="",
                title=r["title"],
                description=r["description"],
                acceptance_criteria=r.get("acceptance_criteria", []),
                source_document="uploaded_document",
            )
            for r in data.get("requirements", [])
        ]
        logger.info("Extracted %d requirements", len(reqs))
        return reqs

    # ── Test case generation ───────────────────────────────────────────────

    def generate_test_cases(
        self,
        requirements: List[Requirement],
        username_selector: str = "#username",
        password_selector: str = "#password",
        submit_selector: str = "",
        max_cases: int = 5,
        dom_snapshot: Optional[Dict] = None,       
        generate_variations: bool = False,         
    ) -> List[TestCase]:
        requirements = requirements[:max_cases]
        logger.info("Generating %d test cases...", len(requirements))

        submit_step = (
            f'{{"action": "click", "selector": "{submit_selector}"}}'
            if submit_selector
            else f'{{"action": "press", "selector": "{password_selector}", "value": "Enter"}}'
        )

        req_list = "\n".join(f"- {r.id}: {r.title}" for r in requirements)

        # Inject DOM snapshot when available (Phase 1)
        dom_section = ""
        if dom_snapshot and not dom_snapshot.get("error"):
            snapshot_text = _format_dom_snapshot(dom_snapshot)
            if snapshot_text:
                dom_section = f"""
{snapshot_text}

Using the selectors above is STRONGLY PREFERRED over guessing.
If the DOM snapshot shows a login form, use its exact field selectors
instead of the defaults listed below.
"""

        # Build variations section (Phase 2)
        variations_section = ""
        if generate_variations:
            variations_section = """
VARIATIONS (Phase 2 — parameterization):
For each test case, add a "variations" array with 2-3 boundary/negative variations.
Each variation must have:
  - "label"            : short human-readable name (e.g. "empty email", "wrong password")
  - "data"             : dict of test_data overrides for this variation
  - "expected_results" : list of strings describing what should happen

Rules for variations:
  ✅ Use HARDCODED values in "data" for negative tests (e.g. {"username": "bad@test.com"})
  ✅ Use "" (empty string) to test blank field validation
  ✅ Use very long strings to test field length limits
  ❌ Do NOT use {{username}} / {{password}} templates in negative variation data
     (they get replaced by real credentials and the negative test would pass as positive)

Example variations:
  "variations": [
    {"label": "empty email",     "data": {"username": "", "password": "Test123!"}, "expected_results": ["validation error shown"]},
    {"label": "wrong password",  "data": {"username": "user@test.com", "password": "wrong123"}, "expected_results": ["error message shown"]},
    {"label": "valid login",     "data": {"username": "{{username}}", "password": "{{password}}"}, "expected_results": ["dashboard loaded"]}
  ]
"""

        prompt = f"""Generate exactly {len(requirements)} Playwright test cases as structured JSON steps.

REQUIREMENTS:
{req_list}
{dom_section}
SELECTORS (use EXACTLY these for login fields — no others):
- username field: {username_selector}
- password field: {password_selector}

═══════════════════════════════════════════════════════════════
ALLOWED ACTIONS  (use ONLY these — any other action is rejected)
═══════════════════════════════════════════════════════════════

── INTERACTION ─────────────────────────────────────────────────
  goto               → navigate to a URL
                       value = full URL or {{{{url}}}}
  fill               → type text into an input field
                       selector + value required
  click              → click a button or link
                       add "force": true to bypass overlay interception
  check              → tick/untick a checkbox or radio  ← use INSTEAD of click for checkboxes
  press              → keyboard key on focused element  (e.g. "Enter", "Tab")
  scroll_to          → scroll element into viewport (lazy-loaded content, long pages)
                       selector required; no value needed
  hover              → hover over element to reveal dropdown or tooltip
                       selector required; no value needed
  select             → choose option from a <select> element
                       selector = the <select>; value = option value or visible label text
  click_text         → click a button or link by its VISIBLE TEXT LABEL
                       value = the text to find  e.g. "Logout", "Sign Out", "Continue"
                       NO selector needed — finds any button, link, or [role=button]
                       ✅ USE THIS for Logout, navigation, and any button whose CSS
                          selector you are not certain about
  wait_for_load_state→ wait for page load; value = "networkidle" (recommended)
  wait_for_timeout   → pause; value = milliseconds as string e.g. "2000"

── ASSERTIONS (Phase 3) ────────────────────────────────────────
  check_url          → assert current URL contains a path fragment
                       value = "/dashboard"          → URL must CONTAIN /dashboard
                       value = "!/dashboard"         → URL must NOT contain /dashboard
                       Prefix "!" = assert absence  (used for negative / error tests)

  check_text         → assert page or element text contains a string
                       selector = CSS selector (optional, defaults to body)
                       value = "Welcome back"
                       value = "!Error occurred"  ← "!" prefix = assert NOT present

  check_element      → assert element is in a specific state
                       selector = element to check
                       value = one of: visible | hidden | enabled | disabled | checked | unchecked

  check_attribute    → assert an element's attribute equals an expected value
                       selector = element
                       value = "attribute=expected"  e.g. "type=email" or "aria-disabled=false"
                       ⚠️  MUST contain "=" — will be dropped if missing

  check_count        → assert the number of elements matching a selector
                       selector = CSS selector
                       value = integer as string e.g. "3" or "0"

═══════════════════════════════════════════════════════════════

TEMPLATE VARIABLES — resolved at runtime from real credentials:
  {{{{url}}}}       → base application URL
  {{{{username}}}}  → real login username
  {{{{password}}}}  → real login password

  ⚠️  Use {{{{username}}}} / {{{{password}}}} ONLY for POSITIVE (success) test cases.
      For NEGATIVE test cases (error messages, rejected logins, rate limiting):
      hardcode specific invalid values directly — e.g. "value": "wrong@test.com"
      so that the real credentials are NOT substituted.

SELECTOR RULES — bad selectors are the #1 cause of test failures:
  ✅ Use: {username_selector}, {password_selector}, button[type='submit'], input[type='submit']
  ✅ Use: [aria-label='...'], [name='...'], #id-you-know-exists, h1, .known-class
  ✅ For checkboxes: use the "check" action with input[type='checkbox'] or input[name='...']
  ❌ NEVER use: data-testid (hallucinated), a[href*='...'] (fragile), .class-you-invented
  ❌ NEVER use wait_for_selector for verification — use check_url or check_text instead

POST-LOGIN NAVIGATION TESTS (logout, profile, settings):
  After login the user lands on a dashboard.
  - Use check_url to verify the dashboard URL first
  - For logout/nav buttons: ALWAYS use click_text with the button's visible label
    e.g. {{"action": "click_text", "value": "Logout"}}
         {{"action": "click_text", "value": "Sign Out"}}
         {{"action": "click_text", "value": "Log out"}}
  - NEVER guess aria-label values — they are app-specific and almost always wrong
  - After logout: check_url "!/dashboard" (URL should no longer contain /dashboard)
                  check_url "/auth"         (or whatever the login page path is)
{variations_section}
STRICT RULES — violations silently drop the test case:
1. First step MUST be: {{"action": "goto", "value": "{{{{url}}}}"}}
2. Use {username_selector} for username fills, {password_selector} for password fills
3. NEVER use data-testid selectors
4. NEVER use wait_for_selector for verification — use check_url or check_text
5. For checkboxes: use the "check" action, NOT "click"
6. For check_attribute, value MUST contain "=" e.g. "type=email"
7. For logout/nav buttons: use click_text, NEVER guess aria-label values
8. For negative URL tests: prefix value with "!" e.g. "!/dashboard"
9. Maximum 10 steps per test case

EXAMPLES:

Positive login test:
  goto → fill username({{{{username}}}}) → fill password({{{{password}}}}) → press Enter
  → wait_for_load_state → check_url "/dashboard" → check_text "Welcome"

Negative login test — URL check with "!" (user stays on auth page):
  goto → fill username("bad@test.com") → fill password("wrongpass") → press Enter
  → wait_for_load_state → check_url "!/dashboard" → check_text "Invalid"
  ← "!/dashboard" means: assert /dashboard is NOT in the current URL

Logout test — use click_text, NOT aria-label guessing:
  goto → fill → fill → press Enter → wait_for_load_state → check_url "/dashboard"
  → click_text "Logout"  ← finds any button/link labelled "Logout", "Log out", "Sign Out"
  → wait_for_load_state → check_url "!/dashboard" → check_url "/auth"

Content / UI verification:
  {{"action": "check_text",      "value": "Dashboard"}}           ← page contains "Dashboard"
  {{"action": "check_text",      "value": "!Server Error"}}       ← page does NOT contain error
  {{"action": "check_element",   "selector": ".alert",    "value": "visible"}}
  {{"action": "check_element",   "selector": "#submit",   "value": "enabled"}}
  {{"action": "check_attribute", "selector": "input#email", "value": "type=email"}}
  {{"action": "check_count",     "selector": ".nav-item", "value": "5"}}

Dropdown / scroll / hover:
  {{"action": "scroll_to",  "selector": "#signup-form"}}
  {{"action": "hover",      "selector": "nav .dropdown"}}
  {{"action": "select",     "selector": "select[name='country']", "value": "United States"}}
  {{"action": "click_text", "value": "Continue"}}                 ← text-based nav click

Checkbox test:
  {{"action": "check", "selector": "input[name='rememberMe']"}}   ← use "check", not "click"

Return ONLY this JSON, no markdown, no explanation:
{{
    "test_cases": [
        {{
            "requirement_id": "REQ-ID",
            "title": "Descriptive test title",
            "steps": [
                {{"action": "goto", "value": "{{{{url}}}}"}},
                {{"action": "fill", "selector": "{username_selector}", "value": "{{{{username}}}}"}},
                {{"action": "fill", "selector": "{password_selector}", "value": "{{{{password}}}}"}},
                {submit_step},
                {{"action": "wait_for_load_state", "value": "networkidle"}},
                {{"action": "check_url", "value": "/dashboard"}},
                {{"action": "check_text", "value": "Welcome"}}
            ],
            "test_data": {{"username": "test@example.com", "password": "TestPass123!"}},
            "expected_results": ["User lands on /dashboard after login", "Welcome message shown"],
            "variations": []
        }}
    ]
}}"""

        text = _call_claude(self.client, prompt, self._model, max_tokens=5000)

        try:
            data = _parse_json(text)
        except ValueError:
            logger.warning("JSON parse failed, retrying with simplified prompt...")
            fallback = (
                f"Return JSON only — {len(requirements)} test cases for: {req_list}\n\n"
                f'{{"test_cases": [{{"requirement_id": "REQ-001", "title": "Login test", '
                f'"steps": [{{"action": "goto", "value": "{{{{url}}}}"}}, '
                f'{{"action": "fill", "selector": "{username_selector}", "value": "{{{{username}}}}"}}, '
                f'{{"action": "fill", "selector": "{password_selector}", "value": "{{{{password}}}}"}}, '
                f'{{"action": "press", "selector": "{password_selector}", "value": "Enter"}}, '
                f'{{"action": "wait_for_load_state", "value": "networkidle"}}], '
                f'"test_data": {{"username": "test@example.com", "password": "Test123!"}}, '
                f'"expected_results": ["passes"], "variations": []}}]}}'
            )
            text = _call_claude(self.client, fallback, self._model, max_tokens=3000)
            data = _parse_json(text)

        test_cases = []
        for tc in data.get("test_cases", []):
            title = tc.get("title", "Untitled")
            raw_steps = tc.get("steps", [])

            try:
                steps = _validate_steps(raw_steps, title)
            except ValueError as e:
                logger.error("Skipping test case '%s': %s", title, e)
                continue

            td = tc.get("test_data", {})
            td.pop("url", None)  # executor injects the real base_url

            display_script = _steps_to_script(raw_steps, td)

            # Phase 2: preserve variations if requested and present
            raw_variations = tc.get("variations", []) if generate_variations else []
            variations = [v for v in raw_variations if isinstance(v, dict)]

            test_cases.append(TestCase(
                id="",
                requirement_id=tc.get("requirement_id", ""),
                title=title,
                steps=steps,
                test_data=td,
                expected_results=tc.get("expected_results", []),
                playwright_script=display_script,
                variations=variations,
            ))

        test_cases = test_cases[:max_cases]
        logger.info("Generated %d valid test cases", len(test_cases))
        return test_cases

    # ── Vision verification (Phase 1) ──────────────────────────────────────

    def analyze_screenshot(
        self,
        screenshot_path: str,
        expected_results: List[str],
    ) -> Dict[str, Any]:
        """
        Use Claude vision to verify that a screenshot matches the expected test results.

        Returns:
            {
                "passed"     : bool   — True if expected results are visually confirmed
                "confidence" : float  — 0.0–1.0
                "explanation": str    — what was observed
            }
        """
        try:
            with open(screenshot_path, "rb") as f:
                image_data = base64.standard_b64encode(f.read()).decode("utf-8")
        except Exception as e:
            logger.error("Vision: cannot read screenshot %s: %s", screenshot_path, e)
            return {"passed": True, "confidence": 0.0, "explanation": f"Cannot read screenshot: {e}"}

        expected_text = "\n".join(f"- {r}" for r in expected_results) if expected_results else "- No specific expectations"

        prompt = f"""You are a QA verification assistant. Analyze this screenshot carefully and
determine whether the expected test results are visually satisfied.

EXPECTED RESULTS:
{expected_text}

Instructions:
1. Describe what you actually see on the page (UI elements, messages, URL if visible).
2. For each expected result, state whether it appears to be satisfied.
3. Give an overall pass/fail verdict.

Return ONLY raw JSON — no markdown, no code fences:
{{
    "passed": true,
    "confidence": 0.95,
    "explanation": "The dashboard page is displayed. Login was successful as shown by the welcome message."
}}"""

        for model in MODELS:
            try:
                resp = self.client.messages.create(
                    model=model,
                    max_tokens=512,
                    messages=[{
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/png",
                                    "data": image_data,
                                },
                            },
                            {"type": "text", "text": prompt},
                        ],
                    }],
                )
                result_text = resp.content[0].text
                parsed = _parse_json(result_text)
                return {
                    "passed": bool(parsed.get("passed", True)),
                    "confidence": float(parsed.get("confidence", 1.0)),
                    "explanation": str(parsed.get("explanation", "")),
                }
            except Exception as e:
                if "not_found_error" in str(e) or "404" in str(e):
                    logger.warning("Vision: model %s unavailable, trying next...", model)
                    continue
                logger.error("Vision verification error with %s: %s", model, e)
                return {
                    "passed": True,
                    "confidence": 0.0,
                    "explanation": f"Vision check error: {e}",
                }

        return {
            "passed": True,
            "confidence": 0.0,
            "explanation": "No vision-capable model available",
        }

    # ── Report generation ──────────────────────────────────────────────────

    def generate_test_report(
        self,
        executions: list,
        requirements: List[Requirement],
    ) -> TestReport:
        logger.info("Generating test report...")
        total = len(executions)
        passed = sum(1 for e in executions if e.status == "passed")
        failed = sum(1 for e in executions if e.status == "failed")
        errors = sum(1 for e in executions if e.status == "error")
        pass_rate = (passed / total * 100) if total else 0

        lines = []
        for e in executions:
            t = f"{e.execution_time:.2f}s" if e.execution_time else "N/A"
            line = f"- {e.test_case_id}: {e.status} ({t})"
            if e.error_message:
                line += f" | {e.error_message[:80]}"
            if getattr(e, "variation_label", None):
                line += f" [variation: {e.variation_label}]"
            if getattr(e, "vision_verdict", None):
                vv = e.vision_verdict
                line += f" [vision: {'✓' if vv.get('passed') else '✗'} conf={vv.get('confidence', 0):.2f}]"
            lines.append(line)

        results_text = "\n".join(lines)
        pass_rate_str = f"{pass_rate:.1f}"

        prompt = f"""Write a QA test report for these execution results.

{results_text}

Pass rate: {passed}/{total} ({pass_rate_str}%)

Return ONLY raw JSON (no markdown, no code fences):
{{
    "summary": "One paragraph executive summary",
    "analysis": "Detailed analysis of failures and patterns",
    "recommendations": ["Actionable recommendation 1", "Actionable recommendation 2"]
}}"""

        report_id = f"REPORT-{__import__('uuid').uuid4().hex[:8].upper()}"
        generated_at = datetime.now()

        try:
            text = _call_claude(self.client, prompt, self._model)
            rd = _parse_json(text)
            summary = rd.get("summary", f"{passed}/{total} tests passed ({pass_rate_str}%)")
            analysis = rd.get("analysis", "See detailed results.")
            recommendations = rd.get("recommendations", ["Review failed tests"])
        except Exception as e:
            logger.error("Report generation LLM call failed: %s", e)
            summary = f"{passed}/{total} tests passed ({pass_rate_str}%)"
            analysis = "Report generation encountered an error. Review execution logs."
            recommendations = ["Review failed tests", "Check selectors match the target application"]

        metrics = {
            "total_tests": total,
            "passed": passed,
            "failed": failed,
            "errors": errors,
            "pass_rate": pass_rate,
        }

        html_content = _build_html_report(
            report_id, generated_at, summary, analysis, recommendations, metrics, executions
        )

        return TestReport(
            id=report_id,
            execution_ids=[e.id for e in executions],
            generated_at=generated_at,
            summary=summary,
            metrics=metrics,
            analysis=analysis,
            recommendations=recommendations,
            html_content=html_content,
        )


# ── HTML report builder ─────────────────────────────────────────────────────

def _build_html_report(
    report_id: str,
    generated_at: datetime,
    summary: str,
    analysis: str,
    recommendations: List[str],
    metrics: Dict[str, Any],
    executions: list,
) -> str:
    rec_items = "".join(f"<li>{r}</li>" for r in recommendations)
    rows = []
    for e in executions:
        t = f"{e.execution_time:.2f}s" if e.execution_time else "N/A"
        colour = {"passed": "#28a745", "failed": "#dc3545"}.get(e.status, "#ffc107")
        err = f"<br><small>{e.error_message[:120]}</small>" if e.error_message else ""

        # Phase 2: show variation label
        variation_cell = ""
        if getattr(e, "variation_label", None):
            variation_cell = f"<br><small><em>Variation: {e.variation_label}</em></small>"

        # Phase 1: show vision verdict
        vision_cell = ""
        vv = getattr(e, "vision_verdict", None)
        if vv:
            icon = "✓" if vv.get("passed") else "✗"
            conf = vv.get("confidence", 0)
            vision_cell = f"<br><small>Vision: {icon} ({conf:.0%}) — {vv.get('explanation', '')[:80]}</small>"

        # Phase 1: show attempt count
        attempts = getattr(e, "attempts", 1)
        attempt_note = f" <small>(attempt {attempts})</small>" if attempts > 1 else ""

        rows.append(
            f"<tr><td>{e.test_case_id}{variation_cell}</td>"
            f'<td style="color:{colour};font-weight:bold">{e.status.upper()}{attempt_note}</td>'
            f"<td>{t}</td>"
            f"<td>{err}{vision_cell}</td></tr>"
        )
    rows_html = "\n".join(rows)
    pass_rate = metrics.get("pass_rate", 0)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>QA Report — {report_id}</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 40px; color: #333; }}
    h1 {{ color: #2c3e50; }} h2 {{ color: #34495e; border-bottom: 1px solid #eee; padding-bottom: 6px; }}
    .metric {{ display: inline-block; margin: 8px 16px 8px 0; padding: 12px 20px;
               border-radius: 6px; background: #f4f6f8; text-align: center; }}
    .metric .val {{ font-size: 28px; font-weight: bold; }}
    .metric .lbl {{ font-size: 12px; color: #777; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border: 1px solid #ddd; padding: 8px 12px; text-align: left; }}
    th {{ background: #f4f6f8; }}
    ul {{ padding-left: 20px; }} li {{ margin: 4px 0; }}
    .footer {{ margin-top: 40px; font-size: 12px; color: #aaa; }}
  </style>
</head>
<body>
  <h1>QA Test Report</h1>
  <p><strong>Report ID:</strong> {report_id} &nbsp;|&nbsp;
     <strong>Generated:</strong> {generated_at.strftime('%Y-%m-%d %H:%M:%S')}</p>

  <h2>Summary</h2>
  <p>{summary}</p>

  <h2>Metrics</h2>
  <div>
    <div class="metric"><div class="val">{metrics['total_tests']}</div><div class="lbl">Total</div></div>
    <div class="metric"><div class="val" style="color:#28a745">{metrics['passed']}</div><div class="lbl">Passed</div></div>
    <div class="metric"><div class="val" style="color:#dc3545">{metrics['failed']}</div><div class="lbl">Failed</div></div>
    <div class="metric"><div class="val" style="color:#ffc107">{metrics['errors']}</div><div class="lbl">Errors</div></div>
    <div class="metric"><div class="val">{pass_rate:.1f}%</div><div class="lbl">Pass Rate</div></div>
  </div>

  <h2>Execution Results</h2>
  <table>
    <tr><th>Test Case</th><th>Status</th><th>Duration</th><th>Notes</th></tr>
    {rows_html}
  </table>

  <h2>Analysis</h2>
  <p>{analysis}</p>

  <h2>Recommendations</h2>
  <ul>{rec_items}</ul>

  <div class="footer">Generated by QA Test Agent · Powered by Claude AI</div>
</body>
</html>"""
