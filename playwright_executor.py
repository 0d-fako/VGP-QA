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
    # ── Modern web: modal / iframe / shadow DOM / uploads / DnD ─────────
    "dismiss_modal", # Close a modal/dialog overlay (Escape + common close-button patterns)
    "iframe_switch", # Switch execution context into an iframe (value: URL fragment, name, index, or CSS sel)
    "iframe_exit",   # Return to the main page context after iframe_switch
    "wait_for_stable", # Wait for selector to be visible AND stop moving (layout stabilised)
    "select_custom", # Handle non-native custom dropdowns: click trigger, then select option by text
    "upload_file",   # Set file-input value — selector: input[type=file], value: absolute/relative file path
    "drag_drop",     # Drag selector element onto target element (target CSS selector in value)
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


async def _resolve_frame(page: Page, frame_ref: str):
    """
    Resolve an iframe by URL fragment, name, integer index, or CSS selector.
    Returns the matching Frame or raises ValueError.
    """
    frames = page.frames
    if frame_ref.isdigit():
        idx = int(frame_ref)
        if idx < len(frames):
            return frames[idx]
        raise ValueError(f"iframe_switch: frame index {idx} out of range (have {len(frames)})")
    for frame in frames:
        if frame_ref in frame.url or frame.name == frame_ref:
            return frame
    # Last resort: locate the <iframe> element by CSS selector and get its content frame
    try:
        el = await page.wait_for_selector(frame_ref, state="attached", timeout=5000)
        content = await el.content_frame()
        if content:
            return content
    except Exception:
        pass
    raise ValueError(f"iframe_switch: could not resolve frame '{frame_ref}'")


async def _dismiss_modal(page: Page, selector: Optional[str], timeout: int) -> None:
    """
    Try to close a modal/dialog overlay.
    Order: explicit selector → Escape key → common close-button patterns.
    """
    if selector:
        try:
            await page.click(selector, timeout=min(timeout, 5000))
            await page.wait_for_timeout(200)
            return
        except Exception:
            pass

    await page.keyboard.press("Escape")
    await page.wait_for_timeout(300)

    close_patterns = [
        '[aria-label="Close"]', '[aria-label="close"]', '[aria-label="Dismiss"]',
        'button:has-text("×")', 'button:has-text("✕")', 'button:has-text("Close")',
        '.modal-close', '.close-button', '.btn-close',
        '[data-dismiss="modal"]', '[data-testid="modal-close"]', '[data-testid="close-button"]',
        'dialog [type="button"]',
    ]
    for pattern in close_patterns:
        try:
            loc = page.locator(pattern).first
            if await loc.is_visible():
                await loc.click(timeout=2000)
                await page.wait_for_timeout(200)
                return
        except Exception:
            continue


def _shadow_locator(ctx, selector: str):
    """
    Resolve a selector that may target elements inside a Shadow DOM.

    Supported formats:
      • 'shadow:host-sel>>>inner-sel'  — explicit shadow-pierce syntax
      • Any selector with '>>'         — passed through; Playwright's CSS engine
                                         pierces shadow roots natively with >>

    All other selectors are passed through unchanged.
    """
    if selector.startswith("shadow:"):
        inner = selector[7:]
        if ">>>" in inner:
            host_sel, _, inner_sel = inner.partition(">>>")
            return ctx.locator(host_sel.strip()).locator(inner_sel.strip())
        return ctx.locator(inner)
    return ctx.locator(selector)


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

        # ctx tracks the current execution context: main Page or a Frame inside an iframe.
        # iframe_switch sets it; iframe_exit resets it back to page.
        # Navigation actions (goto, wait_for_load_state) always use the main page.
        ctx = page  # type: ignore[assignment]  # Page | Frame share the locator API

        for step_idx, step in enumerate(steps):
            action = step.action
            if action not in ALLOWED_ACTIONS:
                raise ValueError(f"Action '{action}' is not in the allowed list.")

            selector = step.selector
            raw_value = step.value or ""
            value = self._resolve(raw_value, merged)
            timeout = step.timeout or self.config.timeout

            # Per-step frame override: if step.frame is set, temporarily resolve
            # that frame for this single step without altering the persistent ctx.
            if step.frame and action not in ("iframe_switch", "iframe_exit"):
                step_ctx = await _resolve_frame(page, step.frame)
            else:
                step_ctx = ctx

            logger.debug("Step: %s | selector=%s | value=%s | frame=%s", action, selector, value, step.frame)

            if action == "goto":
                url = value if value else merged["url"]
                await page.goto(url, timeout=timeout)

            elif action == "fill":
                # REQ 7.5: try exact selector first, then broader fallbacks
                filled = False
                try:
                    await step_ctx.wait_for_selector(selector, state="visible", timeout=timeout)
                    await step_ctx.fill(selector, value)
                    filled = True
                except Exception as fill_err:
                    if any(k in str(fill_err).lower() for k in ("timeout", "not found", "selector")):
                        logger.warning(
                            "fill: primary selector '%s' failed (%s), trying fallbacks…",
                            selector, str(fill_err)[:80],
                        )
                        # Fallback 1: getByPlaceholder (if selector looks like a label)
                        try:
                            loc = step_ctx.get_by_placeholder(selector.strip("#").strip("[]"), exact=False)
                            if await loc.count():
                                await loc.first.fill(value, timeout=timeout)
                                filled = True
                                logger.info("fill: succeeded via placeholder fallback for '%s'", selector)
                        except Exception:
                            pass
                        # Fallback 2: getByLabel
                        if not filled:
                            try:
                                loc = step_ctx.get_by_label(selector.strip("#").strip("[]"), exact=False)
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
                    await step_ctx.wait_for_selector(selector, state="visible", timeout=timeout)
                    # force=True bypasses pointer-event interception (e.g. Tailwind overlay components)
                    await step_ctx.click(selector, timeout=timeout, force=step.force)
                    clicked = True
                except Exception as click_err:
                    if any(k in str(click_err).lower() for k in ("timeout", "not found", "selector")):
                        logger.warning(
                            "click: primary selector '%s' failed (%s), trying text fallback…",
                            selector, str(click_err)[:80],
                        )
                        try:
                            found = await step_ctx.evaluate(
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
                await step_ctx.wait_for_selector(selector, timeout=timeout)
                try:
                    await step_ctx.check(selector, timeout=timeout, force=step.force)
                except Exception as check_err:
                    intercept_keywords = ("intercepts pointer events", "timeout", "TimeoutError")
                    if any(kw.lower() in str(check_err).lower() for kw in intercept_keywords):
                        logger.warning(
                            "page.check() failed (%s: %s) — falling back to JS .click()",
                            type(check_err).__name__, str(check_err)[:80],
                        )
                        await step_ctx.locator(selector).evaluate("el => el.click()")
                    else:
                        raise

            elif action == "press":
                key = value if value else "Enter"
                await step_ctx.press(selector, key)

            elif action == "wait_for_selector":
                await step_ctx.wait_for_selector(selector, timeout=timeout)

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
                raw = raw_value
                negate = raw.startswith("!")
                search_raw = raw[1:] if negate else raw
                search_text = self._resolve(search_raw, merged)
                target_sel = selector or "body"
                try:
                    content = await step_ctx.locator(target_sel).text_content(timeout=timeout) or ""
                except Exception:
                    content = await step_ctx.inner_text("body") if target_sel == "body" else ""
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
                state = value.strip().lower() if value else "visible"
                loc = step_ctx.locator(selector)
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
                actual_attr = await step_ctx.get_attribute(selector, attr_name, timeout=timeout)
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
                actual_count = await step_ctx.locator(selector).count()
                if actual_count != expected_count:
                    raise AssertionError(
                        f"check_count failed: '{selector}' found {actual_count} elements, "
                        f"expected {expected_count}"
                    )
                logger.info("check_count passed: '%s' has %d element(s)", selector, expected_count)

            elif action == "scroll_to":
                await _shadow_locator(step_ctx, selector).scroll_into_view_if_needed(timeout=timeout)
                logger.info("scroll_to: '%s' scrolled into viewport", selector)

            elif action == "hover":
                await step_ctx.wait_for_selector(selector, state="visible", timeout=timeout)
                await step_ctx.hover(selector, timeout=timeout)
                logger.info("hover: over '%s'", selector)

            elif action == "select":
                await step_ctx.wait_for_selector(selector, state="visible", timeout=timeout)
                try:
                    await step_ctx.select_option(selector, value=value, timeout=timeout)
                except Exception:
                    await step_ctx.select_option(selector, label=value, timeout=timeout)
                logger.info("select: '%s' → option '%s'", selector, value)

            elif action == "click_text":
                text = value or ""
                clicked = False
                # 1. Try role=button with matching name
                btn_loc = step_ctx.get_by_role("button", name=text, exact=False)
                if await btn_loc.count():
                    await btn_loc.first.wait_for(state="visible", timeout=timeout)
                    await btn_loc.first.click(timeout=timeout)
                    clicked = True
                # 2. Try role=link with matching name
                if not clicked:
                    link_loc = step_ctx.get_by_role("link", name=text, exact=False)
                    if await link_loc.count():
                        await link_loc.first.wait_for(state="visible", timeout=timeout)
                        await link_loc.first.click(timeout=timeout)
                        clicked = True
                # 3. Try any element that contains the text
                if not clicked:
                    text_loc = step_ctx.get_by_text(text, exact=False)
                    if await text_loc.count():
                        await text_loc.first.wait_for(state="visible", timeout=timeout)
                        await text_loc.first.click(timeout=timeout)
                        clicked = True
                # 4. JS fallback — searches all interactive elements for matching text
                if not clicked:
                    found = await step_ctx.evaluate(
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

            # ── Modal detection & dismiss ──────────────────────────────────

            elif action == "dismiss_modal":
                await _dismiss_modal(page, selector, timeout)
                logger.info("dismiss_modal: modal dismissed (selector=%s)", selector)

            # ── iFrame context switch ──────────────────────────────────────

            elif action == "iframe_switch":
                # value or selector identifies the frame (URL fragment / name / index / CSS)
                frame_ref = value or selector or ""
                ctx = await _resolve_frame(page, frame_ref)
                logger.info("iframe_switch: switched to frame '%s'", frame_ref)

            elif action == "iframe_exit":
                ctx = page
                logger.info("iframe_exit: returned to main page context")

            # ── Shadow DOM & dynamic loading ───────────────────────────────

            elif action == "wait_for_stable":
                # Wait until the element is visible and its bounding rect stops changing.
                await page.wait_for_selector(selector, state="visible", timeout=timeout)
                await page.evaluate(
                    """(sel) => new Promise(resolve => {
                        const el = document.querySelector(sel);
                        if (!el) { resolve(); return; }
                        let last = JSON.stringify(el.getBoundingClientRect());
                        let streak = 0;
                        const t = setInterval(() => {
                            const cur = JSON.stringify(el.getBoundingClientRect());
                            streak = cur === last ? streak + 1 : 0;
                            last = cur;
                            if (streak >= 4) { clearInterval(t); resolve(); }
                        }, 80);
                        setTimeout(() => { clearInterval(t); resolve(); }, Math.min(3000, %d));
                    })""" % min(timeout, 5000),
                    selector,
                )
                logger.info("wait_for_stable: '%s' layout stabilised", selector)

            # ── Custom dropdown (non-native) ───────────────────────────────

            elif action == "select_custom":
                # selector = dropdown trigger; value = option text to select
                target = _shadow_locator(ctx, selector) if selector else None
                if target is None:
                    raise ValueError("select_custom: selector is required")
                await target.wait_for(state="visible", timeout=timeout)
                await target.click(timeout=timeout)
                await page.wait_for_timeout(350)  # let the dropdown animate open

                # Try ARIA option role first, then common list-item patterns
                opt_found = False
                for opt_loc in [
                    page.get_by_role("option", name=value, exact=False),
                    page.locator(f'[role="listbox"] [role="option"]:has-text("{value}")'),
                    page.locator(f'[role="menu"] [role="menuitem"]:has-text("{value}")'),
                    page.locator(f'ul li:has-text("{value}")'),
                    page.get_by_text(value, exact=False),
                ]:
                    try:
                        if await opt_loc.count():
                            visible = opt_loc.first
                            if await visible.is_visible():
                                await visible.click(timeout=timeout)
                                opt_found = True
                                break
                    except Exception:
                        continue
                if not opt_found:
                    raise AssertionError(
                        f"select_custom: option '{value}' not found in dropdown triggered by '{selector}'"
                    )
                logger.info("select_custom: '%s' → '%s'", selector, value)

            # ── File upload ────────────────────────────────────────────────

            elif action == "upload_file":
                # selector = file input element; value = path(s) to upload
                # Multiple files: separate paths with '|' in value
                paths = [p.strip() for p in value.split("|")] if "|" in value else value
                await page.set_input_files(selector, paths, timeout=timeout)
                logger.info("upload_file: '%s' ← %s", selector, paths)

            # ── Drag and drop ──────────────────────────────────────────────

            elif action == "drag_drop":
                # selector = source element; value = target element selector
                if not value:
                    raise ValueError("drag_drop: value must contain the target element selector")
                await page.drag_and_drop(selector, value, timeout=timeout)
                logger.info("drag_drop: '%s' → '%s'", selector, value)

            # ── Per-step frame override: if step.frame is set, use it just for this step ──
            # (This runs BEFORE the screenshot block but AFTER all action dispatches above,
            #  because iframe_switch / iframe_exit already handled their own ctx updates.)

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
