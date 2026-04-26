import base64
import copy
import os
import tempfile
from typing import Dict, List, Optional, Tuple

from models import Requirement, TestCase, TestStep


class WorkflowService:
    """Orchestrates LLM calls and business logic. No Streamlit imports."""

    def __init__(self, llm, storage):
        self._llm = llm
        self._storage = storage

    # ── Rate limiting ──────────────────────────────────────────────────────

    @property
    def api_call_count(self) -> int:
        return self._llm.api_call_count

    def rate_limit_exceeded(self) -> bool:
        return self._llm.rate_limit_exceeded()

    # ── File / image helpers ───────────────────────────────────────────────

    def extract_file_content(self, file_obj) -> str:
        """Extract text from a PDF, DOCX, TXT, or MD file-like object."""
        fpath = None
        try:
            suffix = f".{file_obj.name.rsplit('.', 1)[-1]}"
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(file_obj.getvalue())
                fpath = tmp.name

            if file_obj.name.endswith(".pdf"):
                import pdfplumber
                blocks = []
                with pdfplumber.open(fpath) as pdf:
                    for page in pdf.pages:
                        t = page.extract_text()
                        if t:
                            blocks.append(t)
                return f"### {file_obj.name}\n" + "\n".join(blocks)
            elif file_obj.name.endswith(".docx"):
                import docx
                doc = docx.Document(fpath)
                return f"### {file_obj.name}\n" + "\n".join(p.text for p in doc.paragraphs)
            else:
                with open(fpath, encoding="utf-8", errors="replace") as fh:
                    return f"### {file_obj.name}\n" + fh.read()
        finally:
            if fpath and os.path.exists(fpath):
                os.unlink(fpath)

    def fetch_image_from_url(self, url: str) -> Tuple[str, str]:
        """Fetch an image URL and return (base64_str, media_type)."""
        import requests as _req
        resp = _req.get(url, timeout=15)
        resp.raise_for_status()
        b64 = base64.standard_b64encode(resp.content).decode()
        ct = resp.headers.get("content-type", "image/png").split(";")[0]
        return b64, ct

    # ── Requirements ──────────────────────────────────────────────────────

    def analyze_requirements(self, content: str) -> List[Requirement]:
        return self._llm.analyze_requirements(content)

    def flag_ambiguous_requirements(self, requirements: List[Requirement]) -> List[dict]:
        return self._llm.flag_ambiguous_requirements(requirements)

    def apply_clarifications(
        self,
        requirements: List[Requirement],
        clarifications: Dict[str, str],
    ) -> List[Requirement]:
        """Return requirements with clarification text appended to descriptions."""
        result = []
        for req in requirements:
            note = clarifications.get(req.id, "").strip()
            if note:
                req_copy = copy.copy(req)
                req_copy.description = req.description + f"\n\nClarification: {note}"
                result.append(req_copy)
            else:
                result.append(req)
        return result

    # ── Design analysis ───────────────────────────────────────────────────

    def analyze_design(
        self,
        image_b64: str,
        requirements: List[Requirement],
        media_type: str = "image/png",
    ) -> dict:
        return self._llm.analyze_design_asset(image_b64, requirements, media_type=media_type)

    # ── Test case generation ──────────────────────────────────────────────

    def generate_test_cases(
        self,
        requirements: List[Requirement],
        *,
        username_selector: str = "#username",
        password_selector: str = "#password",
        submit_selector: str = "",
        max_cases: int = 5,
        dom_snapshot: Optional[dict] = None,
        generate_variations: bool = False,
        design_context: Optional[str] = None,
    ) -> List[TestCase]:
        return self._llm.generate_test_cases(
            requirements,
            username_selector=username_selector,
            password_selector=password_selector,
            submit_selector=submit_selector,
            max_cases=max_cases,
            dom_snapshot=dom_snapshot,
            generate_variations=generate_variations,
            design_context=design_context,
        )

    def regenerate_one(
        self,
        tc: TestCase,
        requirements: List[Requirement],
        *,
        username_selector: str = "#username",
        password_selector: str = "#password",
        submit_selector: str = "",
        dom_snapshot: Optional[dict] = None,
        design_context: Optional[str] = None,
    ) -> Tuple[Optional[TestCase], Optional[str]]:
        """Re-generate a single test case from its source requirement."""
        req_match = next((r for r in requirements if r.id == tc.requirement_id), None)
        if not req_match:
            return None, "No source requirement found."
        try:
            new_tcs = self._llm.generate_test_cases(
                [req_match],
                username_selector=username_selector,
                password_selector=password_selector,
                submit_selector=submit_selector,
                max_cases=1,
                dom_snapshot=dom_snapshot,
                design_context=design_context,
            )
            if new_tcs:
                new_tcs[0].id = tc.id
                new_tcs[0].suite = tc.suite
                new_tcs[0].approved = False
                return new_tcs[0], None
        except Exception as exc:
            return None, str(exc)
        return None, "No test case generated."

    # ── Post-generation enrichment ─────────────────────────────────────────

    def inject_external_data(
        self, test_cases: List[TestCase], ext_data: List[dict]
    ) -> None:
        """Append external data rows as variations (mutates in place)."""
        for tc in test_cases:
            for i, row in enumerate(ext_data):
                tc.variations.append({
                    "label":            f"ext-data-row-{i + 1}",
                    "data":             dict(row),
                    "expected_results": tc.expected_results,
                })

    def inject_custom_assertions(
        self, test_cases: List[TestCase], rules: List[str]
    ) -> None:
        """Append custom assertion steps to each test case (mutates in place)."""
        for tc in test_cases:
            for rule in rules:
                if "=" in rule:
                    tc.steps.append(
                        TestStep(action="check_attribute", selector="body", value=rule)
                    )
                else:
                    tc.steps.append(TestStep(action="check_text", value=rule))

    # ── Screenshot analysis ───────────────────────────────────────────────

    def analyze_screenshot(self, screenshot_path: str, expected_results: list) -> dict:
        return self._llm.analyze_screenshot(screenshot_path, expected_results)
