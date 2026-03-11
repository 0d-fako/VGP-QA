from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from datetime import datetime
import uuid


@dataclass
class TestStep:
    """A single whitelisted Playwright action step."""
    action: str              # goto | fill | click | check | press | wait_for_selector | wait_for_load_state | wait_for_timeout | check_url
    selector: Optional[str] = None  # CSS selector (for fill, click, check, press, wait_for_selector)
    value: Optional[str] = None     # URL/text/key/state/ms — supports {{url}}, {{username}}, {{password}}
    timeout: Optional[int] = None   # Override default timeout (ms)
    force: bool = False      # click only: bypass pointer-event interception (e.g. Tailwind overlay checkboxes)


@dataclass
class Requirement:
    """Represents a testable requirement from documentation."""
    id: str
    title: str
    description: str
    acceptance_criteria: List[str]
    source_document: str
    created_at: datetime = None

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now()
        if not self.id:
            self.id = f"REQ-{uuid.uuid4().hex[:8].upper()}"


@dataclass
class TestCase:
    """Represents a generated test case."""
    id: str
    requirement_id: str
    title: str
    steps: List[TestStep]           # Structured steps for execution (whitelisted)
    test_data: Dict[str, Any]
    expected_results: List[str]
    playwright_script: str = ""     # Human-readable display only — never executed
    variations: List[Dict[str, Any]] = field(default_factory=list)  # Phase 2: parameterization
    created_at: datetime = None

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now()
        if not self.id:
            self.id = f"TC-{uuid.uuid4().hex[:8].upper()}"


@dataclass
class TestExecution:
    """Represents a test execution result."""
    id: str
    test_case_id: str
    status: str          # running | passed | failed | error
    start_time: datetime
    end_time: Optional[datetime]
    screenshots: List[str]
    logs: List[str]
    error_message: Optional[str] = None
    error_type: Optional[str] = None        # timeout | assertion | auth | selector | network | unknown
    execution_time: Optional[float] = None   # Set explicitly by executor after end_time is known
    attempts: int = 1                        # Phase 1: number of execution attempts (retry tracking)
    vision_verdict: Optional[Dict] = None   # Phase 1: {"passed": bool, "confidence": float, "explanation": str}
    variation_index: Optional[int] = None   # Phase 2: which variation (0-based index)
    variation_label: Optional[str] = None   # Phase 2: human-readable variation description

    def __post_init__(self):
        if not self.id:
            self.id = f"EXEC-{uuid.uuid4().hex[:8].upper()}"


@dataclass
class TestReport:
    """Represents a test execution report."""
    id: str
    execution_ids: List[str]
    generated_at: datetime
    summary: str
    metrics: Dict[str, Any]
    analysis: str
    recommendations: List[str]
    html_content: str

    def __post_init__(self):
        if not self.id:
            self.id = f"REPORT-{uuid.uuid4().hex[:8].upper()}"


@dataclass
class PlaywrightConfig:
    """Configuration for Playwright execution."""
    base_url: str
    browser: str = "chromium"       # chromium | firefox | webkit
    headless: bool = True
    timeout: int = 30000
    viewport: Dict[str, int] = None
    credentials: Optional[Dict[str, str]] = None
    max_retries: int = 0            # Phase 1: number of retries on timeout failures (0 = no retry)
    auth_type: str = "form"         # Phase 2: form | cookie | token
    shared_session: bool = False    # Phase 2: share one browser context across the entire test suite

    def __post_init__(self):
        if self.viewport is None:
            self.viewport = {"width": 1920, "height": 1080}
