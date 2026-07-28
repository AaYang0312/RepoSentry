from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Severity(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ReviewMode(str, Enum):
    SINGLE = "single"
    TEAM = "team"
    SWARM = "swarm"


class RunStatus(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"
    BUDGET_EXCEEDED = "budget_exceeded"


@dataclass(frozen=True)
class ToolCall:
    call_id: str
    name: str
    arguments: Dict[str, Any]


@dataclass(frozen=True)
class ModelMessage:
    role: str
    content: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)
    tool_call_id: Optional[str] = None


@dataclass(frozen=True)
class ModelResponse:
    content: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)
    total_tokens: int = 0


@dataclass(frozen=True)
class Evidence:
    path: str
    line_start: int
    line_end: Optional[int] = None
    symbol: Optional[str] = None
    excerpt: Optional[str] = None

    def is_well_formed(self) -> bool:
        if not self.path or self.path.startswith("/") or ".." in self.path.split("/"):
            return False
        if self.line_start < 1:
            return False
        return self.line_end is None or self.line_end >= self.line_start


@dataclass
class Finding:
    category: str
    summary: str
    severity: Severity
    confidence: float
    evidence: List[Evidence]
    agent: str
    recommendation: Optional[str] = None
    finding_id: str = field(default_factory=lambda: str(uuid4()))

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["severity"] = self.severity.value
        return value


@dataclass(frozen=True)
class AnalysisRequest:
    repository_path: str
    pr_number: Optional[int] = None
    changed_files: List[str] = field(default_factory=list)
    additions: int = 0
    deletions: int = 0
    dependency_changed: bool = False
    api_contract_changed: bool = False
    sensitive_paths: List[str] = field(default_factory=list)
    # Phase 2: server-derived revision pair. When both are present, routing is
    # driven by ``change_set`` and the manual risk booleans above are ignored.
    base_revision: Optional[str] = None
    head_revision: Optional[str] = None
    change_set: Optional[Dict[str, Any]] = None

    @property
    def changed_lines(self) -> int:
        return self.additions + self.deletions

    @property
    def has_revision_pair(self) -> bool:
        """True when routing should be grounded in a server-derived change set."""

        return bool(self.base_revision and self.head_revision)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RouteDecision:
    mode: ReviewMode
    score: int
    reasons: List[str]
    selected_agents: List[str]

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["mode"] = self.mode.value
        return value


@dataclass
class AgentRunResult:
    agent: str
    status: RunStatus
    output: str
    steps: int
    tool_calls: int
    total_tokens: int
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["status"] = self.status.value
        return value


@dataclass
class AnalysisReport:
    task_id: str
    route: RouteDecision
    summary: str
    findings: List[Finding]
    agent_results: List[AgentRunResult]
    rejected_findings: List[Dict[str, Any]]
    created_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "route": self.route.to_dict(),
            "summary": self.summary,
            "findings": [item.to_dict() for item in self.findings],
            "agent_results": [item.to_dict() for item in self.agent_results],
            "rejected_findings": self.rejected_findings,
            "created_at": self.created_at,
        }

