from datetime import datetime
from typing import Generator, List, Optional, Tuple

from models import PlaywrightConfig, TestCase, TestExecution
from playwright_executor import SyncPlaywrightExecutor


class ExecutionController:
    """Runs test cases via Playwright and persists results. No Streamlit imports."""

    def __init__(self, playwright_config: PlaywrightConfig, storage, db=None):
        self._config = playwright_config
        self._storage = storage
        self._db = db

    def iter_run(
        self,
        test_cases: List[TestCase],
        *,
        use_variations: bool = False,
        vision_fn=None,
    ) -> Generator[Tuple[TestCase, TestExecution], None, None]:
        """Yield (tc, execution) after each test / variation completes."""
        executor = SyncPlaywrightExecutor(self._config)

        if use_variations:
            for tc in test_cases:
                results = executor.execute_test_case_with_variations(tc, vision_fn=vision_fn)
                for res in results:
                    self._storage.upload_execution_evidence(res)
                    yield tc, res
        else:
            for tc in test_cases:
                result = executor.execute_test_case(tc, vision_fn=vision_fn)
                self._storage.upload_execution_evidence(result)
                yield tc, result

    def save_run(
        self,
        run_name: str,
        requirements: list,
        test_cases: list,
        executions: list,
        existing_run_id: Optional[str] = None,
    ) -> Optional[str]:
        """Persist run to DB. Returns run_id (new) or existing_run_id (update)."""
        if not self._db:
            return None
        if existing_run_id:
            self._db.update_run(existing_run_id, executions=executions)
            return existing_run_id
        return self._db.save_run(
            name=run_name,
            requirements=requirements,
            test_cases=test_cases,
            executions=executions,
        )

    @staticmethod
    def default_run_name(base_url: str) -> str:
        return f"{datetime.now().strftime('%Y-%m-%d %H:%M')} — {base_url}"
