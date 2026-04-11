import os
import logging
import asyncio
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import List, Dict, Any, Optional, Callable

from playwright.async_api import async_playwright, Page, Browser, BrowserContext

from config import config
from models import TestCase, TestStep, TestExecution, PlaywrightConfig

logger = logging.getLogger(__name__)

# Allowed Playwright actions — anything else raises ValueError (never executed).
ALLOWED_ACTIONS = {
    # ── Navigation & interaction ──────────────────────────────────────────
    "goto", "fill", "click", "check", "press",
    "wait_for_selector", "wait_for_load_state", "wait_for_timeout",
    "scroll_to",     # Scroll element into viewport (lazy-loaded content, long pages)
    "hover",         # Hover to reveal dropdowns / tooltips
    "select",        # Choose option in <select> by value or visible label text
    "click_text",    # Click a button/link by its VISIBLE TEXT — no CSS selector needed
    # ── Assertions (Phase 3) ─────────────────────────────────────────────
    "check_url",     # Assert current URL contains (or !not) a path fragment
    "check_text",    # Assert page/element text contains (or !not) a string
    "check_element", # Assert element state: visible | hidden | enabled | disabled | checked | unchecked
    "check_attribute", # Assert element attribute: "attr=expected_value"
    "check_count",   # Assert count of matching elements equals expected integer
}


def _categorize_error(err: Exception) -> str:
    """
    Map a raw exception to one of six human-readable error categories.

    Categories (used in UI badges and DB analytics):
        timeout   — Playwright timed out waiting for a selector or page load
        assertion — A check_* step's condition was not met
        auth      — Login/authentication step failed
        selector  — Element could not be found (not a timeout)
        network   — DNS / connection-level failure
        unknown   — Anything else
    """
    err_str   = str(err)
    err_type  = type(err).__name__

    if "timeout" in err_str.lower() or "TimeoutError" in err_type:
        return "timeout"
    if isinstance(err, AssertionError) or "check_" in err_str or "check failed" in err_str.lower():
        return "assertion"
    if "Authentication failed" in err_str or "auth" in err_str.lower():
        return "auth"
    if any(k in err_str.lower() for k in ("ERR_NAME_NOT_RESOLVED", "net::ERR", "connection refused", "ECONNREFUSED")):
        return "network"
    if any(k in err_str.lower() for k in ("selector", "element", "locator", "not found", "no element")):
        return "selector"
    return "unknown"


def get_metrics(executions: List[TestExecution]) -> dict:
    """Pure function — no Playwright instance needed."""
    total = len(executions)
    passed = sum(1 for e in executions if e.status == "passed")
    failed = sum(1 for e in executions if e.status == "failed")
    errors = sum(1 for e in executions if e.status == "error")
    times = [e.execution_time for e in executions if e.execution_time is not None]
    total_time = sum(times)
    return {
        "total_executions": total,
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "pass_rate": (passed / total * 100) if total else 0,
        "total_execution_time": total_time,
        "average_execution_time": (total_time / total) if total else 0,
    }


# ── DOM Inspection (Phase 1) ─────────────────────────────────────────────────

async def _inspect_dom_async(
    base_url: str,
    browser_type: str = "chromium",
    headless: bool = True,
    timeout: int = 30000,
    credentials: Optional[Dict] = None,
) -> Dict:
    """
    Navigate to base_url headlessly and extract real interactive elements.
    If credentials are provided, authenticate first so the post-login DOM is captured.
    Returns a dict with: url, title, inputs, buttons, forms, headings, error.
    """
    result: Dict[str, Any] = {
        "url": base_url,
        "title": "",
        "forms": [],
        "inputs": [],
        "buttons": [],
        "headings": [],
        "error": None,
    }
    try:
        async with async_playwright() as p:
            bt = getattr(p, browser_type)
            launch_args = ["--no-sandbox", "--disable-dev-shm-usage"] if browser_type == "chromium" else []
            browser = await bt.launch(headless=headless, args=launch_args)
            try:
                page = await browser.new_page()
                page.set_default_timeout(timeout)

                # Optionally authenticate before inspecting
                if credentials:
                    login_url = credentials.get("login_url", "").strip()
                    if login_url:
                        if not login_url.startswith("http"):
                            login_url = base_url.rstrip("/") + "/" + login_url.lstrip("/")
                        await page.goto(login_url, timeout=timeout)
                        await page.wait_for_load_state("networkidle", timeout=10000)

                    u_sel = credentials.get("username_selector", "#username") or "#username"
                    p_sel = credentials.get("password_selector", "#password") or "#password"
                    s_sel = credentials.get("submit_selector", "")
                    username = credentials.get("username", "")
                    password = credentials.get("password", "")

                    if username:
                        await page.wait_for_selector(u_sel, state="visible", timeout=10000)
                        await page.fill(u_sel, username)
                    if password:
                        await page.wait_for_selector(p_sel, state="visible", timeout=10000)
                        await page.fill(p_sel, password)
                    if s_sel:
                        await page.click(s_sel)
                    else:
                        await page.press(p_sel, "Enter")
                    try:
                        await page.wait_for_load_state("networkidle", timeout=10000)
                    except Exception:
                        await page.wait_for_timeout(2000)

                await page.goto(base_url, timeout=timeout)
                await page.wait_for_load_state("networkidle", timeout=10000)

                result["url"] = page.url
                result["title"] = await page.title()

                # Extract real DOM selectors via JavaScript
                dom_data = await page.evaluate("""() => {
                    function getSelector(el) {
                        if (el.id) return '#' + el.id;
                        if (el.name) return el.tagName.toLowerCase() + '[name="' + el.name + '"]';
                        if (el.getAttribute('aria-label')) return '[aria-label="' + el.getAttribute('aria-label') + '"]';
                        if (el.type && el.type !== 'text') return el.tagName.toLowerCase() + '[type="' + el.type + '"]';
                        const cls = el.className && typeof el.className === 'string' ? el.className.trim().split(/\\s+/)[0] : '';
                        return el.tagName.toLowerCase() + (cls ? '.' + cls : '');
                    }

                    const inputs = Array.from(document.querySelectorAll('input, textarea, select'))
                        .filter(el => el.type !== 'hidden' && el.offsetParent !== null)
                        .slice(0, 20)
                        .map(el => ({
                            tag: el.tagName.toLowerCase(),
                            type: el.type || '',
                            name: el.name || '',
                            id: el.id || '',
                            placeholder: el.placeholder || '',
                            selector: getSelector(el),
                            label: (() => {
                                if (el.id) {
                                    const lbl = document.querySelector('label[for="' + el.id + '"]');
                                    if (lbl) return lbl.textContent.trim();
                                }
                                const parent = el.closest('label');
                                return parent ? parent.textContent.trim().slice(0, 40) : '';
                            })()
                        }));

                    const buttons = Array.from(document.querySelectorAll('button, input[type="submit"], input[type="button"], a[href]'))
                        .filter(el => el.offsetParent !== null)
                        .slice(0, 15)
                        .map(el => ({
                            tag: el.tagName.toLowerCase(),
                            type: el.type || '',
                            text: el.textContent.trim().slice(0, 60),
                            selector: getSelector(el),
                            href: el.href || ''
                        }));

                    const forms = Array.from(document.querySelectorAll('form'))
                        .slice(0, 5)
                        .map(f => ({
                            id: f.id || '',
                            action: f.action || '',
                            method: f.method || 'get',
                            fields: Array.from(f.querySelectorAll('input:not([type=hidden]), select, textarea'))
                                .map(el => getSelector(el))
                        }));

                    const headings = Array.from(document.querySelectorAll('h1, h2, h3'))
                        .slice(0, 6)
                        .map(h => h.textContent.trim())
                        .filter(t => t.length > 0);

                    return { inputs, buttons, forms, headings };
                }""")

                result["inputs"] = dom_data.get("inputs", [])
                result["buttons"] = dom_data.get("buttons", [])
                result["forms"] = dom_data.get("forms", [])
                result["headings"] = dom_data.get("headings", [])
            finally:
                await browser.close()

    except Exception as e:
        result["error"] = str(e)
        logger.error("DOM inspection failed: %s\n%s", e, traceback.format_exc())

    return result


def inspect_dom(
    base_url: str,
    browser_type: str = "chromium",
    headless: bool = True,
    timeout: int = 30000,
    credentials: Optional[Dict] = None,
) -> Dict:
    """Synchronous wrapper — runs _inspect_dom_async in an isolated thread/event-loop."""
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(
            asyncio.run,
            _inspect_dom_async(base_url, browser_type, headless, timeout, credentials),
        ).result()


# ── Main executor ────────────────────────────────────────────────────────────

class PlaywrightExecutor:
    """Async Playwright executor — runs inside its own thread/event-loop."""

    def __init__(self, playwright_config: PlaywrightConfig):
        self.config = playwright_config
        self.screenshots_dir = config.SCREENSHOTS_DIR
        os.makedirs(self.screenshots_dir, exist_ok=True)

    # ── Public entry point ─────────────────────────────────────────────────

    async def execute_test_case(
        self,
        test_case: TestCase,
        vision_fn: Optional[Callable] = None,
    ) -> TestExecution:
        """
        Execute a single test case, retrying on timeouts up to max_retries times.
        Optionally run vision verification on the final screenshot (Phase 1).
        """
        start_time = datetime.now()
        execution = TestExecution(
            id="",
            test_case_id=test_case.id,
            status="running",
            start_time=start_time,
            end_time=None,
            screenshots=[],
            logs=[],
            attempts=1,
        )
        logger.info("Executing test case: %s", test_case.title)

        max_attempts = max(1, self.config.max_retries + 1)
        last_error = None

        for attempt in range(1, max_attempts + 1):
            execution.attempts = attempt
            if attempt > 1:
                logger.info("Retry %d/%d for: %s", attempt, max_attempts, test_case.title)
                execution.screenshots = []  # Clear screenshots from the previous attempt

            try:
                async with async_playwright() as p:
                    browser = await self._launch_browser(p)
                    try:
                        context = await browser.new_context(viewport=self.config.viewport)
                        page = await context.new_page()
                        page.set_default_timeout(self.config.timeout)

                        # Authenticate first if configured
                        if self.config.credentials:
                            try:
                                await self._authenticate_context(context, page)
                            except Exception as auth_err:
                                msg = f"Authentication failed: {auth_err}"
                                logger.error(msg)
                                execution.status = "error"
                                execution.error_message = msg
                                execution.error_type = "auth"
                                await self._screenshot(page, execution, "auth_failure")
                                return execution  # Auth failures don't benefit from retrying

                        # Navigate to base URL and take initial screenshot
                        await page.goto(self.config.base_url, timeout=30000)
                        await page.wait_for_load_state("networkidle", timeout=10000)
                        await self._screenshot(page, execution, "initial")

                        # Execute structured steps
                        try:
                            await self._execute_steps(page, test_case.steps, test_case.test_data, execution)
                            execution.status = "passed"
                            last_error = None
                        except Exception as step_err:
                            err_str = str(step_err)
                            last_error = err_str
                            is_timeout = (
                                "timeout" in err_str.lower()
                                or "TimeoutError" in type(step_err).__name__
                            )
                            if is_timeout and attempt < max_attempts:
                                logger.warning(
                                    "Timeout on attempt %d, will retry: %s",
                                    attempt, err_str[:100],
                                )
                                execution.status = "running"
                                await self._screenshot(page, execution, f"timeout_attempt_{attempt}")
                            else:
                                execution.status = "failed"
                                execution.error_message = err_str
                                execution.error_type = _categorize_error(step_err)
                                logger.error("Test failed: %s", step_err)
                                await self._screenshot(page, execution, "failure")

                        await self._screenshot(page, execution, "final")
                    finally:
                        await context.close()
                        await browser.close()

            except Exception as e:
                last_error = str(e)
                is_timeout = "timeout" in str(e).lower()
                if is_timeout and attempt < max_attempts:
                    logger.warning("Execution timeout on attempt %d, will retry", attempt)
                    execution.status = "running"
                else:
                    execution.status = "error"
                    execution.error_message = str(e)
                    execution.error_type = _categorize_error(e)
                    logger.error("Execution error: %s\n%s", e, traceback.format_exc())

            # Stop retrying once we have a conclusive result
            if execution.status in ("passed", "failed", "error"):
                break

        # If we exhausted retries but never got a conclusive status
        if execution.status == "running":
            execution.status = "failed"
            execution.error_message = last_error or "Max retries exceeded"
            execution.error_type = "timeout"

        # Vision verification on the final screenshot (Phase 1)
        if vision_fn and execution.screenshots and execution.status != "error":
            final_screenshot = execution.screenshots[-1]
            if os.path.exists(final_screenshot):
                try:
                    verdict = vision_fn(final_screenshot, test_case.expected_results)
                    execution.vision_verdict = verdict
                    vision_passed = verdict.get("passed", True)
                    vision_conf   = verdict.get("confidence", 0.0)
                    logger.info(
                        "Vision verdict for '%s': passed=%s (confidence=%.2f)",
                        test_case.title,
                        vision_passed,
                        vision_conf,
                    )
                    # Downgrade: passed → failed when vision disagrees
                    if execution.status == "passed" and not vision_passed:
                        execution.status = "failed"
                        execution.error_message = (
                            f"Vision check failed: {verdict.get('explanation', '')}"
                        )
                    # Upgrade: failed → passed when the failure was a selector/timeout
                    # issue (test quality, not app quality) AND vision is highly confident
                    # the page looks correct.  Assertion/auth/network failures are NOT
                    # upgraded because they indicate a real application problem.
                    elif (
                        execution.status == "failed"
                        and execution.error_type in ("selector", "timeout")
                        and vision_passed
                        and vision_conf >= 0.8
                    ):
                        logger.info(
                            "Vision upgrade for '%s': selector/timeout failure overridden "
                            "by high-confidence vision pass (conf=%.2f). "
                            "Original error: %s",
                            test_case.title, vision_conf, execution.error_message,
                        )
                        execution.status = "passed"
                        execution.error_message = None
                        execution.error_type = None
                except Exception as ve:
                    logger.error("Vision verification failed: %s", ve)

        execution.end_time = datetime.now()
        execution.execution_time = (execution.end_time - execution.start_time).total_seconds()
        return execution

    async def execute_suite(
        self,
        test_cases: List[TestCase],
        vision_fn: Optional[Callable] = None,
    ) -> List[TestExecution]:
        """Run all test cases. Uses shared session if configured (Phase 2)."""
        if self.config.shared_session:
            return await self._execute_suite_shared(test_cases, vision_fn)

        results = []
        for i, tc in enumerate(test_cases, 1):
            logger.info("Running test %d/%d: %s", i, len(test_cases), tc.title)
            results.append(await self.execute_test_case(tc, vision_fn=vision_fn))
            await asyncio.sleep(2)
        return results

    # ── Variation execution (Phase 2) ──────────────────────────────────────

    async def execute_test_case_with_variations(
        self,
        test_case: TestCase,
        vision_fn: Optional[Callable] = None,
    ) -> List[TestExecution]:
        """
        Run a test case once per variation (Phase 2: parameterization).
        Each variation overlays its 'data' dict on top of the base test_data.
        If no variations are defined, runs once with base test_data.
        Returns a list of TestExecution results — one per variation.
        """
        if not test_case.variations:
            ex = await self.execute_test_case(test_case, vision_fn=vision_fn)
            return [ex]

        results = []
        for i, variation in enumerate(test_case.variations):
            label = variation.get("label", f"variation_{i + 1}")
            logger.info(
                "Variation %d/%d: %s — %s",
                i + 1, len(test_case.variations), test_case.title, label,
            )

            # Merge base test_data with variation-specific overrides
            merged_data = {**test_case.test_data, **variation.get("data", {})}

            # Build a temporary TestCase carrying this variation's data
            variant = TestCase(
                id=test_case.id,
                requirement_id=test_case.requirement_id,
                title=f"{test_case.title} [{label}]",
                steps=test_case.steps,
                test_data=merged_data,
                expected_results=variation.get("expected_results", test_case.expected_results),
                playwright_script=test_case.playwright_script,
            )

            ex = await self.execute_test_case(variant, vision_fn=vision_fn)
            # Tag the execution with variation metadata
            ex.variation_index = i
            ex.variation_label = label
            results.append(ex)
            await asyncio.sleep(1)

        return results

    # ── Shared-session suite execution (Phase 2) ───────────────────────────

    async def _execute_suite_shared(
        self,
        test_cases: List[TestCase],
        vision_fn: Optional[Callable] = None,
    ) -> List[TestExecution]:
        """
        Run all test cases sharing one browser context.
        Authentication happens once at the start, saving time on repeated logins.
        """
        results = []

        try:
            async with async_playwright() as p:
                browser = await self._launch_browser(p)
                try:
                    context = await browser.new_context(viewport=self.config.viewport)

                    # Authenticate once for the entire suite
                    if self.config.credentials:
                        auth_page = await context.new_page()
                        auth_page.set_default_timeout(self.config.timeout)
                        try:
                            await self._authenticate_context(context, auth_page)
                            await auth_page.close()
                            logger.info("Shared session: authentication succeeded")
                        except Exception as auth_err:
                            logger.error("Shared session auth failed: %s", auth_err)
                            await auth_page.close()
                            for tc in test_cases:
                                ex = TestExecution(
                                    id="",
                                    test_case_id=tc.id,
                                    status="error",
                                    start_time=datetime.now(),
                                    end_time=datetime.now(),
                                    screenshots=[],
                                    logs=[],
                                    error_message=f"Shared session auth failed: {auth_err}",
                                    execution_time=0.0,
                                )
                                results.append(ex)
                            return results

                    # Run each test in the shared context
                    for i, tc in enumerate(test_cases, 1):
                        logger.info(
                            "Shared session: test %d/%d: %s", i, len(test_cases), tc.title
                        )
                        start_time = datetime.now()
                        execution = TestExecution(
                            id="",
                            test_case_id=tc.id,
                            status="running",
                            start_time=start_time,
                            end_time=None,
                            screenshots=[],
                            logs=[],
                        )
                        try:
                            page = await context.new_page()
                            page.set_default_timeout(self.config.timeout)

                            await page.goto(self.config.base_url, timeout=30000)
                            await page.wait_for_load_state("networkidle", timeout=10000)
                            await self._screenshot(page, execution, "initial")

                            try:
                                await self._execute_steps(page, tc.steps, tc.test_data, execution)
                                execution.status = "passed"
                            except Exception as step_err:
                                execution.status = "failed"
                                execution.error_message = str(step_err)
                                logger.error("Test failed: %s", step_err)
                                await self._screenshot(page, execution, "failure")

                            await self._screenshot(page, execution, "final")

                            # Vision check
                            if vision_fn and execution.screenshots and execution.status != "error":
                                final_ss = execution.screenshots[-1]
                                if os.path.exists(final_ss):
                                    try:
                                        verdict = vision_fn(final_ss, tc.expected_results)
                                        execution.vision_verdict = verdict
                                        vision_passed = verdict.get("passed", True)
                                        vision_conf   = verdict.get("confidence", 0.0)
                                        # Downgrade: passed → failed
                                        if execution.status == "passed" and not vision_passed:
                                            execution.status = "failed"
                                            execution.error_message = (
                                                f"Vision check failed: {verdict.get('explanation', '')}"
                                            )
                                        # Upgrade: failed → passed for selector/timeout failures
                                        elif (
                                            execution.status == "failed"
                                            and execution.error_type in ("selector", "timeout")
                                            and vision_passed
                                            and vision_conf >= 0.8
                                        ):
                                            logger.info(
                                                "Vision upgrade for '%s': selector/timeout failure "
                                                "overridden by high-confidence vision pass (conf=%.2f).",
                                                tc.title, vision_conf,
                                            )
                                            execution.status = "passed"
                                            execution.error_message = None
                                            execution.error_type = None
                                    except Exception as ve:
                                        logger.error("Vision verification failed: %s", ve)

                            await page.close()

                        except Exception as e:
                            execution.status = "error"
                            execution.error_message = str(e)
                            logger.error("Execution error: %s", e)
                        finally:
                            execution.end_time = datetime.now()
                            execution.execution_time = (
                                execution.end_time - execution.start_time
                            ).total_seconds()

                        results.append(execution)
                        await asyncio.sleep(1)

                finally:
                    await context.close()
                    await browser.close()

        except Exception as e:
            logger.error("Shared suite fatal error: %s\n%s", e, traceback.format_exc())

        return results

    # ── Step interpreter ───────────────────────────────────────────────────

    async def _execute_steps(
        self,
        page: Page,
        steps: List[TestStep],
        test_data: Dict[str, Any],
        execution: "TestExecution | None" = None,   # REQ 5.1: passed in for per-step screenshots
    ) -> None:
        """
        Interpret structured steps against a whitelisted set of Playwright calls.
        No exec(), no eval(), no arbitrary code.
        """
        # Start with the base URL and LLM-generated defaults, then let the
        # user-provided sidebar credentials override {{username}} / {{password}}
        # so real credentials always win over LLM-generated placeholder values.
        merged = {"url": self.config.base_url.rstrip("/"), **test_data}
        if self.config.credentials:
            creds = self.config.credentials
            if creds.get("username"):
                merged["username"] = creds["username"]
            if creds.get("password"):
                merged["password"] = creds["password"]

        for step_idx, step in enumerate(steps):
            action = step.action
            if action not in ALLOWED_ACTIONS:
                raise ValueError(f"Action '{action}' is not in the allowed list.")

            selector = step.selector
            raw_value = step.value or ""
            value = self._resolve(raw_value, merged)
            timeout = step.timeout or self.config.timeout

            logger.debug("Step: %s | selector=%s | value=%s", action, selector, value)

            if action == "goto":
                url = value if value else merged["url"]
                await page.goto(url, timeout=timeout)

            elif action == "fill":
                # REQ 7.5: try exact selector first, then broader fallbacks
                filled = False
                try:
                    await page.wait_for_selector(selector, state="visible", timeout=timeout)
                    await page.fill(selector, value)
                    filled = True
                except Exception as fill_err:
                    if any(k in str(fill_err).lower() for k in ("timeout", "not found", "selector")):
                        logger.warning(
                            "fill: primary selector '%s' failed (%s), trying fallbacks…",
                            selector, str(fill_err)[:80],
                        )
                        # Fallback 1: getByPlaceholder (if selector looks like a label)
                        try:
                            loc = page.get_by_placeholder(selector.strip("#").strip("[]"), exact=False)
                            if await loc.count():
                                await loc.first.fill(value, timeout=timeout)
                                filled = True
                                logger.info("fill: succeeded via placeholder fallback for '%s'", selector)
                        except Exception:
                            pass
                        # Fallback 2: getByLabel
                        if not filled:
                            try:
                                loc = page.get_by_label(selector.strip("#").strip("[]"), exact=False)
                                if await loc.count():
                                    await loc.first.fill(value, timeout=timeout)
                                    filled = True
                                    logger.info("fill: succeeded via label fallback for '%s'", selector)
                            except Exception:
                                pass
                    if not filled:
                        raise

            elif action == "click":
                # REQ 7.5: try exact CSS selector, then text-based fallback
                clicked = False
                try:
                    await page.wait_for_selector(selector, state="visible", timeout=timeout)
                    # force=True bypasses pointer-event interception (e.g. Tailwind overlay components)
                    await page.click(selector, timeout=timeout, force=step.force)
                    clicked = True
                except Exception as click_err:
                    if any(k in str(click_err).lower() for k in ("timeout", "not found", "selector")):
                        logger.warning(
                            "click: primary selector '%s' failed (%s), trying text fallback…",
                            selector, str(click_err)[:80],
                        )
                        # Fallback: try finding by visible text extracted from selector
                        # e.g. button.submit → look for a button with role + try JS
                        try:
                            found = await page.evaluate(
                                """(sel) => {
                                    try {
                                        const el = document.querySelector(sel);
                                        if (el) { el.click(); return true; }
                                    } catch(e) {}
                                    return false;
                                }""",
                                selector,
                            )
                            if found:
                                clicked = True
                                logger.info("click: succeeded via JS querySelector fallback for '%s'", selector)
                        except Exception:
                            pass
                    if not clicked:
                        raise

            elif action == "check":
                # Dedicated checkbox/radio action.
                # Uses step.force=True to bypass Tailwind/custom overlay interception.
                # If page.check() still fails (e.g. aria-hidden overlay), falls back to
                # a direct JavaScript click on the underlying input element.
                await page.wait_for_selector(selector, timeout=timeout)
                try:
                    await page.check(selector, timeout=timeout, force=step.force)
                except Exception as check_err:
                    intercept_keywords = ("intercepts pointer events", "timeout", "TimeoutError")
                    if any(kw.lower() in str(check_err).lower() for kw in intercept_keywords):
                        logger.warning(
                            "page.check() failed (%s: %s) — falling back to JS .click()",
                            type(check_err).__name__, str(check_err)[:80],
                        )
                        # Direct JS click on the checkbox element, bypassing all overlays
                        await page.locator(selector).evaluate("el => el.click()")
                    else:
                        raise

            elif action == "press":
                key = value if value else "Enter"
                await page.press(selector, key)

            elif action == "wait_for_selector":
                await page.wait_for_selector(selector, timeout=timeout)

            elif action == "wait_for_load_state":
                state = value if value in {"load", "domcontentloaded", "networkidle"} else "networkidle"
                await page.wait_for_load_state(state, timeout=timeout)

            elif action == "wait_for_timeout":
                try:
                    ms = int(value)
                except (ValueError, TypeError):
                    ms = 1000
                await page.wait_for_timeout(ms)

            elif action == "check_url":
                # Reliable post-navigation verification — no DOM selectors needed.
                # Prefix value with "!" to assert the pattern is NOT in the URL.
                current_url = page.url
                pattern = value or ""
                if pattern:
                    negate = pattern.startswith("!")
                    check_pattern = pattern[1:] if negate else pattern
                    found = check_pattern in current_url
                    if negate:
                        if found:
                            raise AssertionError(
                                f"URL check failed: expected '{check_pattern}' NOT in "
                                f"current URL '{current_url}' but it was found"
                            )
                        logger.info(
                            "check_url (absent) passed: '%s' not in '%s'",
                            check_pattern, current_url,
                        )
                    else:
                        if not found:
                            raise AssertionError(
                                f"URL check failed: expected '{check_pattern}' "
                                f"in current URL '{current_url}'"
                            )
                        logger.info(
                            "check_url passed: '%s' found in '%s'",
                            check_pattern, current_url,
                        )

            # ── Phase 3 assertions ────────────────────────────────────────

            elif action == "check_text":
                # value: text to find in the page or element.
                # Prefix with "!" to assert the text is NOT present.
                # selector is optional (defaults to "body").
                raw = raw_value  # use raw_value before _resolve so "!" is preserved
                negate = raw.startswith("!")
                search_raw = raw[1:] if negate else raw
                search_text = self._resolve(search_raw, merged)
                target_sel = selector or "body"
                try:
                    content = await page.locator(target_sel).text_content(timeout=timeout) or ""
                except Exception:
                    # Fallback to full page HTML text if the locator fails
                    content = await page.inner_text("body") if target_sel == "body" else ""
                found = search_text.lower() in content.lower()
                if negate:
                    if found:
                        raise AssertionError(
                            f"check_text failed: text '{search_text}' was found "
                            f"in '{target_sel}' but should NOT be present"
                        )
                    logger.info("check_text (absent) passed: '%s' not found in '%s'", search_text, target_sel)
                else:
                    if not found:
                        raise AssertionError(
                            f"check_text failed: text '{search_text}' not found "
                            f"in '{target_sel}'"
                        )
                    logger.info("check_text passed: '%s' found in '%s'", search_text, target_sel)

            elif action == "check_element":
                # value: "visible" | "hidden" | "enabled" | "disabled" | "checked" | "unchecked"
                state = value.strip().lower() if value else "visible"
                loc = page.locator(selector)
                if state == "visible":
                    if not await loc.is_visible():
                        raise AssertionError(f"check_element failed: '{selector}' is not visible")
                elif state == "hidden":
                    if not await loc.is_hidden():
                        raise AssertionError(f"check_element failed: '{selector}' is not hidden")
                elif state == "enabled":
                    if not await loc.is_enabled():
                        raise AssertionError(f"check_element failed: '{selector}' is not enabled")
                elif state == "disabled":
                    if not await loc.is_disabled():
                        raise AssertionError(f"check_element failed: '{selector}' is not disabled")
                elif state == "checked":
                    if not await loc.is_checked():
                        raise AssertionError(f"check_element failed: '{selector}' is not checked")
                elif state == "unchecked":
                    if await loc.is_checked():
                        raise AssertionError(f"check_element failed: '{selector}' is still checked (expected unchecked)")
                else:
                    raise ValueError(
                        f"check_element: unknown state '{state}'. "
                        "Use: visible | hidden | enabled | disabled | checked | unchecked"
                    )
                logger.info("check_element passed: '%s' is '%s'", selector, state)

            elif action == "check_attribute":
                # value format: "attribute=expected_value"
                # Examples: "type=email",  "aria-disabled=false",  "href=/dashboard"
                if "=" not in value:
                    raise ValueError(
                        f"check_attribute: value must be 'attribute=expected' but got '{value}'"
                    )
                attr_name, _, expected_attr = value.partition("=")
                attr_name = attr_name.strip()
                expected_attr = self._resolve(expected_attr.strip(), merged)
                actual_attr = await page.get_attribute(selector, attr_name, timeout=timeout)
                if actual_attr is None:
                    raise AssertionError(
                        f"check_attribute failed: '{selector}' has no attribute '{attr_name}'"
                    )
                if actual_attr != expected_attr:
                    raise AssertionError(
                        f"check_attribute failed: '{selector}'[{attr_name}] = '{actual_attr}', "
                        f"expected '{expected_attr}'"
                    )
                logger.info("check_attribute passed: '%s'[%s] == '%s'", selector, attr_name, expected_attr)

            elif action == "check_count":
                # value: expected integer number of matching elements
                try:
                    expected_count = int(value)
                except (ValueError, TypeError):
                    raise ValueError(f"check_count: value must be an integer, got '{value}'")
                actual_count = await page.locator(selector).count()
                if actual_count != expected_count:
                    raise AssertionError(
                        f"check_count failed: '{selector}' found {actual_count} elements, "
                        f"expected {expected_count}"
                    )
                logger.info("check_count passed: '%s' has %d element(s)", selector, expected_count)

            elif action == "scroll_to":
                # Scroll the matched element into view — needed for lazy-loaded content.
                await page.locator(selector).scroll_into_view_if_needed(timeout=timeout)
                logger.info("scroll_to: '%s' scrolled into viewport", selector)

            elif action == "hover":
                # Move the mouse over an element to reveal dropdowns, tooltips, etc.
                await page.wait_for_selector(selector, state="visible", timeout=timeout)
                await page.hover(selector, timeout=timeout)
                logger.info("hover: over '%s'", selector)

            elif action == "select":
                # Choose an option in a <select> element by value attribute or label text.
                await page.wait_for_selector(selector, state="visible", timeout=timeout)
                try:
                    await page.select_option(selector, value=value, timeout=timeout)
                except Exception:
                    # Fallback: try matching by visible label text
                    await page.select_option(selector, label=value, timeout=timeout)
                logger.info("select: '%s' → option '%s'", selector, value)

            elif action == "click_text":
                # Click a button or link by its visible text content.
                # This is the RECOMMENDED way to click logout, nav, and action buttons
                # when you know the label but not the CSS selector.
                # Tries: button role → link role → generic text → JS fallback.
                text = value or ""
                clicked = False
                # 1. Try role=button with matching name
                btn_loc = page.get_by_role("button", name=text, exact=False)
                if await btn_loc.count():
                    await btn_loc.first.wait_for(state="visible", timeout=timeout)
                    await btn_loc.first.click(timeout=timeout)
                    clicked = True
                # 2. Try role=link with matching name
                if not clicked:
                    link_loc = page.get_by_role("link", name=text, exact=False)
                    if await link_loc.count():
                        await link_loc.first.wait_for(state="visible", timeout=timeout)
                        await link_loc.first.click(timeout=timeout)
                        clicked = True
                # 3. Try any element that contains the text
                if not clicked:
                    text_loc = page.get_by_text(text, exact=False)
                    if await text_loc.count():
                        await text_loc.first.wait_for(state="visible", timeout=timeout)
                        await text_loc.first.click(timeout=timeout)
                        clicked = True
                # 4. JS fallback — searches all interactive elements for matching text
                if not clicked:
                    found = await page.evaluate(
                        """(text) => {
                            const tags = ['button', 'a', '[role="button"]', '[role="link"]'];
                            const els = [...document.querySelectorAll(tags.join(','))];
                            const match = els.find(
                                el => el.textContent.trim().toLowerCase().includes(text.toLowerCase())
                            );
                            if (match) { match.click(); return true; }
                            return false;
                        }""",
                        text,
                    )
                    if not found:
                        raise AssertionError(
                            f"click_text failed: no visible button/link with text '{text}'"
                        )
                    clicked = True
                logger.info("click_text: clicked element with text '%s'", text)

            # ── REQ 5.1: per-step screenshot ──────────────────────────────
            if self.config.per_step_screenshots and execution is not None:
                try:
                    await self._screenshot(page, execution, f"step_{step_idx + 1:02d}_{action}")
                except Exception as ss_err:
                    logger.debug("Per-step screenshot failed (non-fatal): %s", ss_err)

    @staticmethod
    def _resolve(template: str, data: Dict[str, Any]) -> str:
        """Replace {{key}} placeholders with values from data dict."""
        for k, v in data.items():
            template = template.replace(f"{{{{{k}}}}}", str(v))
        return template

    # ── Browser launch ─────────────────────────────────────────────────────

    async def _launch_browser(self, p) -> Browser:
        bt = getattr(p, self.config.browser)
        if self.config.browser == "chromium":
            return await bt.launch(
                headless=self.config.headless,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
        return await bt.launch(headless=self.config.headless)

    # ── Authentication (Phase 2: form | cookie | token) ────────────────────

    async def _authenticate_context(self, context: BrowserContext, page: Page) -> None:
        """
        Authenticate using the configured auth_type:
          - "form"   : fill username/password form and submit (default)
          - "cookie" : inject pre-built cookies into the browser context
          - "token"  : inject an Authorization Bearer token as an HTTP header
        """
        creds = self.config.credentials
        if not creds:
            return

        auth_type = self.config.auth_type

        if auth_type == "cookie":
            raw_cookies = creds.get("cookies")
            if raw_cookies:
                if isinstance(raw_cookies, str):
                    import json
                    try:
                        raw_cookies = json.loads(raw_cookies)
                    except json.JSONDecodeError:
                        logger.error("Cookie auth: 'cookies' value is not valid JSON")
                        return
                await context.add_cookies(raw_cookies)
                logger.info("Cookie auth: injected %d cookie(s)", len(raw_cookies))
            return

        if auth_type == "token":
            token = creds.get("token", "").strip()
            if token:
                await context.set_extra_http_headers({"Authorization": f"Bearer {token}"})
                logger.info("Token auth: injected Authorization header")
            return

        # Default: "form" auth — navigate to login page and submit credentials
        login_url = creds.get("login_url", "").strip()
        if login_url:
            if not login_url.startswith("http"):
                login_url = self.config.base_url.rstrip("/") + "/" + login_url.lstrip("/")
            logger.info("Navigating to login page: %s", login_url)
            await page.goto(login_url, timeout=30000)
            await page.wait_for_load_state("networkidle", timeout=10000)

        u_sel = creds.get("username_selector", "").strip() or "#username"
        p_sel = creds.get("password_selector", "").strip() or "#password"
        s_sel = creds.get("submit_selector", "").strip()
        username = creds.get("username", "")
        password = creds.get("password", "")

        if username:
            logger.info("Filling username with selector: %s", u_sel)
            await page.wait_for_selector(u_sel, state="visible", timeout=10000)
            await page.fill(u_sel, username)

        if password:
            logger.info("Filling password with selector: %s", p_sel)
            await page.wait_for_selector(p_sel, state="visible", timeout=10000)
            await page.fill(p_sel, password)

        if s_sel:
            await page.click(s_sel)
        else:
            await page.press(p_sel, "Enter")

        try:
            await page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            await page.wait_for_timeout(2000)

    # ── Screenshot helper ──────────────────────────────────────────────────

    async def _screenshot(self, page: Page, execution: TestExecution, label: str) -> None:
        try:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            path = os.path.join(
                self.screenshots_dir,
                f"{execution.test_case_id}_{label}_{ts}.png",
            )
            await page.screenshot(path=path, full_page=True)
            execution.screenshots.append(path)
            logger.info("Screenshot saved: %s", os.path.basename(path))
        except Exception as e:
            logger.error("Screenshot failed: %s", e)


# ── Concurrency guard ────────────────────────────────────────────────────────
# Hard cap on simultaneous Chromium instances to prevent OOM in production.
# Tune via MAX_BROWSER_CONCURRENCY env var (default: 3).
_MAX_BROWSER_CONCURRENCY = int(os.environ.get("MAX_BROWSER_CONCURRENCY", "3"))
_browser_semaphore = threading.Semaphore(_MAX_BROWSER_CONCURRENCY)


# ── Synchronous wrapper ─────────────────────────────────────────────────────

class SyncPlaywrightExecutor:
    """
    Thread-safe synchronous wrapper around PlaywrightExecutor.

    Each call dispatches the coroutine to a dedicated worker thread that owns
    its own event loop, avoiding conflicts with Streamlit's running event loop
    on Windows and other platforms.

    A module-level Semaphore caps simultaneous browser launches to
    MAX_BROWSER_CONCURRENCY (default 3) to prevent OOM under concurrent load.
    """

    def __init__(self, playwright_config: PlaywrightConfig):
        self.executor = PlaywrightExecutor(playwright_config)

    def _run(self, coro):
        """Run a coroutine in an isolated thread with its own event loop."""
        with _browser_semaphore:
            with ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(asyncio.run, coro).result()

    def execute_test_case(
        self,
        tc: TestCase,
        vision_fn: Optional[Callable] = None,
    ) -> TestExecution:
        return self._run(self.executor.execute_test_case(tc, vision_fn=vision_fn))

    def execute_test_suite(
        self,
        tcs: List[TestCase],
        vision_fn: Optional[Callable] = None,
    ) -> List[TestExecution]:
        return self._run(self.executor.execute_suite(tcs, vision_fn=vision_fn))

    def execute_test_case_with_variations(
        self,
        tc: TestCase,
        vision_fn: Optional[Callable] = None,
    ) -> List[TestExecution]:
        """Run a test case against all its defined variations (Phase 2)."""
        return self._run(
            self.executor.execute_test_case_with_variations(tc, vision_fn=vision_fn)
        )
