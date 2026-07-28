"""Turn a revision pair into a server-derived ``ChangeSet``.

The service sits between the read-only Git skill and the analysis pipeline. It
validates refs, honors the ``REPOSENTRY_REPOSITORY_ROOT`` containment rule used
by :class:`AnalysisService`, and projects the resulting change set onto an
:class:`AnalysisRequest` so the router no longer trusts user-supplied risk
booleans.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from reposentry.domain.changes import ChangeSet
from reposentry.domain.models import AnalysisRequest
from reposentry.settings import Settings
from reposentry.skills.git import GitClient, GitError


class RevisionService:
    """Resolve and diff a local revision pair into a :class:`ChangeSet`."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self._settings = settings or Settings.from_env()

    def parse(
        self,
        base_ref: str,
        head_ref: str,
        repository_path: str,
    ) -> ChangeSet:
        root = self._resolve_repository(repository_path)
        client = GitClient(root)
        return client.diff(base_ref, head_ref)

    def build_request(
        self,
        repository_path: str,
        change_set: ChangeSet,
        pr_number: Optional[int] = None,
    ) -> AnalysisRequest:
        """Project a ``ChangeSet`` onto an :class:`AnalysisRequest`.

        The router reads the server-derived booleans from ``change_set`` when
        ``base_revision``/``head_revision`` are set; the legacy risk booleans on
        the request are mirrored purely for backward-compatible reporting.
        """

        route_inputs = change_set.to_dict()["route_inputs"]
        return AnalysisRequest(
            repository_path=repository_path,
            pr_number=pr_number,
            changed_files=change_set.changed_files,
            additions=change_set.additions,
            deletions=change_set.deletions,
            dependency_changed=route_inputs["dependency_changed"],
            api_contract_changed=route_inputs["api_contract_changed"],
            sensitive_paths=list(route_inputs["sensitive_paths"]),
            base_revision=change_set.base.sha,
            head_revision=change_set.head.sha,
            change_set=change_set.to_dict(),
        )

    # -- containment --------------------------------------------------------

    def _resolve_repository(self, requested_path: str) -> Path:
        requested = Path(requested_path).expanduser().resolve()
        allowed_root_value = self._settings.repository_root
        if not allowed_root_value:
            return requested
        allowed_root = Path(allowed_root_value).expanduser().resolve()
        try:
            requested.relative_to(allowed_root)
        except ValueError as exc:
            raise GitError(
                "repository path is outside REPOSENTRY_REPOSITORY_ROOT"
            ) from exc
        return requested


def attach_change_set(
    request: AnalysisRequest,
    change_set_dict: Dict[str, Any],
) -> AnalysisRequest:
    """Return a copy of ``request`` with the server-derived change set attached.

    ``AnalysisRequest`` is frozen, so we rebuild it. Fields that the change set
    can authoritatively replace (file list, additions, deletions, risk flags)
    are overwritten; everything else is preserved.
    """

    route_inputs = change_set_dict.get("route_inputs", {})
    return AnalysisRequest(
        repository_path=request.repository_path,
        pr_number=request.pr_number,
        changed_files=change_set_dict.get("changed_files", []),
        additions=int(change_set_dict.get("additions", 0)),
        deletions=int(change_set_dict.get("deletions", 0)),
        dependency_changed=bool(route_inputs.get("dependency_changed", False)),
        api_contract_changed=bool(route_inputs.get("api_contract_changed", False)),
        sensitive_paths=list(route_inputs.get("sensitive_paths", [])),
        base_revision=request.base_revision,
        head_revision=request.head_revision,
        change_set=change_set_dict,
    )
