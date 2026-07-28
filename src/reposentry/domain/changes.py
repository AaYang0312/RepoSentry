"""Domain objects describing a real local change set derived from Git.

These value objects travel from the read-only Git skill through the revision
service into the router, so routing decisions are grounded in repository facts
instead of user-supplied risk booleans. They mirror the dataclass style of
``domain/models.py`` and stay free of FastAPI/Pydantic imports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from reposentry.domain.models import utc_now_iso


# ---------------------------------------------------------------------------
# Path heuristics
#
# Pure functions over a file path so the detectors are unit-testable without a
# working tree. Keep the patterns conservative: false positives here cost an
# extra agent; false negatives cost a missed review.
# ---------------------------------------------------------------------------

DEPENDENCY_PATHS = {
    "requirements.txt",
    "requirements.lock",
    "pyproject.toml",
    "poetry.lock",
    "package.json",
    "package-lock.json",
    "yarn.lock",
    "go.mod",
    "go.sum",
    "cargo.toml",
    "cargo.lock",
    "pdm.lock",
    "uv.lock",
}

API_CONTRACT_NAMES = {
    "openapi.yaml",
    "openapi.yml",
    "swagger.yaml",
    "swagger.yml",
    "routes.py",
    "schemas.py",
    "controllers.py",
}

SENSITIVE_PATH_PARTS = ("auth", "security", "crypto", "secrets", "credentials")
SENSITIVE_FILE_NAMES = {"settings.py", "dockerfile", ".env"}


def is_dependency_path(path: str) -> bool:
    """Return True when the path touches a dependency manifest or lockfile."""

    if not path:
        return False
    name = path.rsplit("/", 1)[-1].lower()
    if name in DEPENDENCY_PATHS:
        return True
    # requirements*.txt / constraints*.txt style globs
    base, dot, _ext = name.rpartition(".")
    if dot and _ext == "txt":
        return base.startswith("requirements") or base.startswith("constraints")
    return False


def is_api_contract_path(path: str) -> bool:
    """Return True when the path likely declares an API contract."""

    if not path:
        return False
    lowered = path.lower()
    name = lowered.rsplit("/", 1)[-1]
    if name in API_CONTRACT_NAMES:
        return True
    parts = lowered.split("/")
    return "api" in parts or "endpoints" in parts


def is_sensitive_path(path: str) -> bool:
    """Return True when the path touches a sensitive area of the repo."""

    if not path:
        return False
    lowered = path.lower()
    name = lowered.rsplit("/", 1)[-1]
    if name in SENSITIVE_FILE_NAMES:
        return True
    if lowered.startswith(".github/workflows/"):
        return True
    parts = lowered.split("/")
    if any(part in SENSITIVE_PATH_PARTS for part in parts):
        return True
    # Catch file/extension forms like ``secrets.yaml`` or ``credentials.json``.
    stem = name.rsplit(".", 1)[0]
    return any(stem == token or stem.startswith(token) for token in SENSITIVE_PATH_PARTS)


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RepositoryRevision:
    """A resolved pointer to a commit in a local repository."""

    repository_path: str
    sha: str

    def to_dict(self) -> Dict[str, Any]:
        return {"repository_path": self.repository_path, "sha": self.sha}


@dataclass(frozen=True)
class DiffHunk:
    """One ``@@ -a,b +c,d @@`` block of a unified diff."""

    old_start: int
    old_len: int
    new_start: int
    new_len: int
    body: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "old_start": self.old_start,
            "old_len": self.old_len,
            "new_start": self.new_start,
            "new_len": self.new_len,
            "body": self.body,
        }


@dataclass(frozen=True)
class DiffEvidence:
    """Diff evidence scoped to a single file, anchored on the head revision."""

    base: RepositoryRevision
    head: RepositoryRevision
    path: str
    line_start: int
    line_end: Optional[int]
    hunks: List[DiffHunk] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "base": self.base.to_dict(),
            "head": self.head.to_dict(),
            "path": self.path,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "hunks": [hunk.to_dict() for hunk in self.hunks],
        }


@dataclass(frozen=True)
class ChangedFile:
    """One repository-relative file changed between two revisions."""

    path: str
    status: str  # one of: added | modified | deleted | renamed
    additions: int = 0
    deletions: int = 0
    hunks: List[DiffHunk] = field(default_factory=list)

    @property
    def changed_lines(self) -> int:
        return self.additions + self.deletions

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "status": self.status,
            "additions": self.additions,
            "deletions": self.deletions,
            "hunks": [hunk.to_dict() for hunk in self.hunks],
        }


@dataclass
class ChangeSet:
    """The aggregate a router consumes: a real diff between two revisions."""

    base: RepositoryRevision
    head: RepositoryRevision
    files: List[ChangedFile] = field(default_factory=list)
    additions: int = 0
    deletions: int = 0
    dependency_changed: bool = False
    api_contract_changed: bool = False
    sensitive_paths: List[str] = field(default_factory=list)
    parsed_at: str = field(default_factory=utc_now_iso)

    # -- derived view -------------------------------------------------------

    @property
    def changed_files(self) -> List[str]:
        return [item.path for item in self.files]

    @property
    def changed_lines(self) -> int:
        return self.additions + self.deletions

    def to_dict(self) -> Dict[str, Any]:
        return {
            "base": self.base.to_dict(),
            "head": self.head.to_dict(),
            "files": [item.to_dict() for item in self.files],
            "changed_files": self.changed_files,
            "additions": self.additions,
            "deletions": self.deletions,
            "changed_lines": self.changed_lines,
            "route_inputs": {
                "dependency_changed": self.dependency_changed,
                "api_contract_changed": self.api_contract_changed,
                "sensitive_paths": list(self.sensitive_paths),
            },
            "parsed_at": self.parsed_at,
        }

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def is_within_root(path: str) -> bool:
        """Repository-relative path guard, mirroring ``Evidence.is_well_formed``."""

        if not path or path.startswith("/"):
            return False
        if ".." in path.split("/"):
            return False
        return True
