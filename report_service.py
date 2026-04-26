from datetime import datetime
from typing import List, Optional

import pandas as pd

from llm_processor import generate_csv_report, generate_junit_xml
from models import TestCase, TestExecution, TestReport


class ReportService:
    """Generates, uploads, persists, and exports test reports. No Streamlit imports."""

    def __init__(self, llm, storage, db=None):
        self._llm = llm
        self._storage = storage
        self._db = db

    def generate(self, executions: List[TestExecution], requirements: list) -> TestReport:
        return self._llm.generate_test_report(executions, requirements)

    def upload(self, report: TestReport) -> str:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        return self._storage.upload_test_report(report.html_content, f"report_{ts}")

    def save_to_db(self, db_run_id: str, report: TestReport) -> None:
        if self._db and db_run_id:
            self._db.update_run(db_run_id, report=report)

    def export_csv(
        self, executions: List[TestExecution], test_cases: List[TestCase]
    ) -> str:
        return generate_csv_report(executions, test_cases)

    def export_junit(
        self, executions: List[TestExecution], test_cases: List[TestCase]
    ) -> str:
        return generate_junit_xml(executions, test_cases)

    def build_traceability_matrix(
        self,
        requirements: list,
        test_cases: List[TestCase],
        executions: List[TestExecution],
    ) -> pd.DataFrame:
        exec_by_tc = {ex.test_case_id: ex for ex in executions}
        rows = []
        for req in requirements:
            linked = [tc for tc in test_cases if tc.requirement_id == req.id]
            if not linked:
                rows.append({
                    "Requirement": f"[{req.id}] {req.title}",
                    "Test Case":   "—",
                    "TC ID":       "—",
                    "Status":      "⚪ Not tested",
                    "Duration":    "—",
                    "Error Type":  "—",
                })
            else:
                for tc in linked:
                    ex = exec_by_tc.get(tc.id)
                    if ex:
                        icon = {"passed": "✅", "failed": "❌", "error": "⚠️"}.get(
                            ex.status, "❓"
                        )
                        status_str = f"{icon} {ex.status.upper()}"
                        dur = f"{ex.execution_time:.2f}s" if ex.execution_time else "—"
                        err = ex.error_type or "—"
                    else:
                        status_str, dur, err = "⚪ Not run", "—", "—"
                    rows.append({
                        "Requirement": f"[{req.id}] {req.title}",
                        "Test Case":   tc.title,
                        "TC ID":       tc.id,
                        "Status":      status_str,
                        "Duration":    dur,
                        "Error Type":  err,
                    })
        return pd.DataFrame(rows)
