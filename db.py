"""
db.py — PostgreSQL persistence layer (Azure-hosted).

Connects to the DATABASE_URL from config and persists test runs, requirements,
test cases, executions, and reports so history survives page refreshes and
container restarts.

Usage:
    db = DatabaseManager()          # raises if DATABASE_URL not set
    db.save_run(run_name, reqs, tcs, executions, report)
    history = db.list_runs()        # [{id, name, created_at, pass_rate, …}]
    row = db.load_run(run_id)       # {name, requirements, test_cases, executions, report}

Tables are created automatically on first connect (CREATE TABLE IF NOT EXISTS).
All complex objects are stored as JSONB so the schema stays flat and flexible.
"""

import json
import logging
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, List, Optional

from config import config
from models import (
    Requirement, TestCase, TestExecution, TestReport, TestStep
)

logger = logging.getLogger(__name__)


# ── Serialisation helpers ────────────────────────────────────────────────────

def _req_to_dict(r: Requirement) -> dict:
    return {
        "id": r.id,
        "title": r.title,
        "description": r.description,
        "acceptance_criteria": r.acceptance_criteria,
        "source_document": r.source_document,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


def _tc_to_dict(tc: TestCase) -> dict:
    return {
        "id": tc.id,
        "requirement_id": tc.requirement_id,
        "title": tc.title,
        "steps": [
            {
                "action": s.action,
                "selector": s.selector,
                "value": s.value,
                "timeout": s.timeout,
                "force": s.force,
            }
            for s in tc.steps
        ],
        "test_data": tc.test_data,
        "expected_results": tc.expected_results,
        "playwright_script": tc.playwright_script,
        "variations": tc.variations,
        "created_at": tc.created_at.isoformat() if tc.created_at else None,
    }


def _exec_to_dict(e: TestExecution) -> dict:
    return {
        "id": e.id,
        "test_case_id": e.test_case_id,
        "status": e.status,
        "start_time": e.start_time.isoformat() if e.start_time else None,
        "end_time": e.end_time.isoformat() if e.end_time else None,
        "screenshots": e.screenshots,
        "logs": e.logs,
        "error_message": e.error_message,
        "error_type": e.error_type,
        "execution_time": e.execution_time,
        "attempts": e.attempts,
        "vision_verdict": e.vision_verdict,
        "variation_index": e.variation_index,
        "variation_label": e.variation_label,
    }


def _report_to_dict(r: TestReport) -> dict:
    return {
        "id": r.id,
        "execution_ids": r.execution_ids,
        "generated_at": r.generated_at.isoformat() if r.generated_at else None,
        "summary": r.summary,
        "metrics": r.metrics,
        "analysis": r.analysis,
        "recommendations": r.recommendations,
        "html_content": r.html_content,
    }


# ── Deserialisation helpers ──────────────────────────────────────────────────

def _dict_to_req(d: dict) -> Requirement:
    return Requirement(
        id=d.get("id", ""),
        title=d.get("title", ""),
        description=d.get("description", ""),
        acceptance_criteria=d.get("acceptance_criteria", []),
        source_document=d.get("source_document", ""),
        created_at=datetime.fromisoformat(d["created_at"]) if d.get("created_at") else None,
    )


def _dict_to_tc(d: dict) -> TestCase:
    steps = [
        TestStep(
            action=s["action"],
            selector=s.get("selector"),
            value=s.get("value"),
            timeout=s.get("timeout"),
            force=bool(s.get("force", False)),
        )
        for s in d.get("steps", [])
    ]
    return TestCase(
        id=d.get("id", ""),
        requirement_id=d.get("requirement_id", ""),
        title=d.get("title", ""),
        steps=steps,
        test_data=d.get("test_data", {}),
        expected_results=d.get("expected_results", []),
        playwright_script=d.get("playwright_script", ""),
        variations=d.get("variations", []),
        created_at=datetime.fromisoformat(d["created_at"]) if d.get("created_at") else None,
    )


def _dict_to_exec(d: dict) -> TestExecution:
    return TestExecution(
        id=d.get("id", ""),
        test_case_id=d.get("test_case_id", ""),
        status=d.get("status", "unknown"),
        start_time=datetime.fromisoformat(d["start_time"]) if d.get("start_time") else datetime.now(),
        end_time=datetime.fromisoformat(d["end_time"]) if d.get("end_time") else None,
        screenshots=d.get("screenshots", []),
        logs=d.get("logs", []),
        error_message=d.get("error_message"),
        error_type=d.get("error_type"),
        execution_time=d.get("execution_time"),
        attempts=d.get("attempts", 1),
        vision_verdict=d.get("vision_verdict"),
        variation_index=d.get("variation_index"),
        variation_label=d.get("variation_label"),
    )


def _dict_to_report(d: dict) -> TestReport:
    return TestReport(
        id=d.get("id", ""),
        execution_ids=d.get("execution_ids", []),
        generated_at=datetime.fromisoformat(d["generated_at"]) if d.get("generated_at") else datetime.now(),
        summary=d.get("summary", ""),
        metrics=d.get("metrics", {}),
        analysis=d.get("analysis", ""),
        recommendations=d.get("recommendations", []),
        html_content=d.get("html_content", ""),
    )


# ── DatabaseManager ──────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS qa_runs (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    requirements  JSONB NOT NULL DEFAULT '[]',
    test_cases    JSONB NOT NULL DEFAULT '[]',
    executions    JSONB NOT NULL DEFAULT '[]',
    report        JSONB
);
"""


class DatabaseManager:
    """
    Thin PostgreSQL client using psycopg2 with a connection pool.
    All data is stored as JSONB so no schema migrations are needed for
    model changes — only the Python ser/deser helpers above change.

    Uses ThreadedConnectionPool (minconn=1, maxconn=5) so multiple Streamlit
    threads share connections without exhausting Postgres max_connections.
    """

    def __init__(self):
        if not config.DATABASE_URL:
            raise ValueError(
                "DATABASE_URL is not set. "
                "Add it to your .env or Streamlit secrets to enable history persistence."
            )
        import psycopg2
        import psycopg2.pool
        import psycopg2.extras
        self._psycopg2 = psycopg2
        self._extras = psycopg2.extras

        self._pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=1,
            maxconn=5,
            dsn=config.DATABASE_URL,
            connect_timeout=10,
        )
        self._ensure_schema()

    @contextmanager
    def _connect(self):
        """Yield a pooled connection; return it on exit."""
        conn = self._pool.getconn()
        try:
            yield conn
        except Exception:
            conn.rollback()
            raise
        finally:
            self._pool.putconn(conn)

    def _ensure_schema(self):
        """Create the qa_runs table if it doesn't exist yet."""
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(_DDL)
                conn.commit()
            logger.info("Database schema verified / created.")
        except Exception as e:
            logger.error("DB schema creation failed: %s", e)
            raise

    # ── Write ────────────────────────────────────────────────────────────────

    def save_run(
        self,
        name: str,
        requirements: List[Requirement],
        test_cases: List[TestCase],
        executions: List[TestExecution],
        report: Optional[TestReport] = None,
    ) -> int:
        """
        Persist a complete test run and return its database row ID.
        If a row with the same name already exists for today it is updated;
        otherwise a new row is inserted.
        """
        reqs_json  = json.dumps([_req_to_dict(r)  for r in requirements])
        tcs_json   = json.dumps([_tc_to_dict(tc)  for tc in test_cases])
        execs_json = json.dumps([_exec_to_dict(e) for e in executions])
        rep_json   = json.dumps(_report_to_dict(report)) if report else None

        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO qa_runs (name, requirements, test_cases, executions, report)
                        VALUES (%s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb)
                        RETURNING id
                        """,
                        (name, reqs_json, tcs_json, execs_json, rep_json),
                    )
                    row_id = cur.fetchone()[0]
                conn.commit()
            logger.info("Saved run '%s' → row id=%d", name, row_id)
            return row_id
        except Exception as e:
            logger.error("DB save_run failed: %s", e)
            raise

    def update_run(
        self,
        run_id: int,
        executions: Optional[List[TestExecution]] = None,
        report: Optional[TestReport] = None,
    ):
        """Update executions and/or report on an existing run (called after tests finish)."""
        updates = []
        params: list = []
        if executions is not None:
            updates.append("executions = %s::jsonb")
            params.append(json.dumps([_exec_to_dict(e) for e in executions]))
        if report is not None:
            updates.append("report = %s::jsonb")
            params.append(json.dumps(_report_to_dict(report)))
        if not updates:
            return
        params.append(run_id)
        sql = f"UPDATE qa_runs SET {', '.join(updates)} WHERE id = %s"
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, params)
                conn.commit()
            logger.info("Updated run id=%d", run_id)
        except Exception as e:
            logger.error("DB update_run failed: %s", e)
            raise

    # ── Read ─────────────────────────────────────────────────────────────────

    def list_runs(self, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Return a lightweight list of runs for the history table.
        Does NOT include full JSONB blobs — just metadata.
        """
        sql = """
            SELECT
                id,
                name,
                created_at,
                jsonb_array_length(executions) AS total_executions,
                (
                    SELECT COUNT(*)
                    FROM jsonb_array_elements(executions) AS e
                    WHERE e->>'status' = 'passed'
                ) AS passed
            FROM qa_runs
            ORDER BY created_at DESC
            LIMIT %s
        """
        try:
            with self._connect() as conn:
                with conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
                    cur.execute(sql, (limit,))
                    rows = cur.fetchall()
            result = []
            for row in rows:
                total = row["total_executions"] or 0
                passed = row["passed"] or 0
                result.append({
                    "id": row["id"],
                    "name": row["name"],
                    "created_at": row["created_at"],
                    "total": total,
                    "passed": passed,
                    "pass_rate": round(passed / total * 100, 1) if total else 0.0,
                })
            return result
        except Exception as e:
            logger.error("DB list_runs failed: %s", e)
            raise

    def load_run(self, run_id: int) -> Dict[str, Any]:
        """
        Load a full run by ID.  Returns a dict with keys:
            name, requirements, test_cases, executions, report (or None)
        """
        sql = "SELECT name, requirements, test_cases, executions, report FROM qa_runs WHERE id = %s"
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, (run_id,))
                    row = cur.fetchone()
            if row is None:
                raise ValueError(f"Run id={run_id} not found")
            name, reqs_raw, tcs_raw, execs_raw, rep_raw = row
            return {
                "name": name,
                "requirements": [_dict_to_req(r)  for r in (reqs_raw  or [])],
                "test_cases":   [_dict_to_tc(tc)  for tc in (tcs_raw   or [])],
                "executions":   [_dict_to_exec(e) for e in (execs_raw or [])],
                "report":       _dict_to_report(rep_raw) if rep_raw else None,
            }
        except Exception as e:
            logger.error("DB load_run failed: %s", e)
            raise

    def delete_run(self, run_id: int):
        """Permanently delete a run. Caller must confirm intent."""
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM qa_runs WHERE id = %s", (run_id,))
                conn.commit()
            logger.info("Deleted run id=%d", run_id)
        except Exception as e:
            logger.error("DB delete_run failed: %s", e)
            raise

    @staticmethod
    def is_configured() -> bool:
        """Return True only when DATABASE_URL is set."""
        return bool(config.DATABASE_URL)
