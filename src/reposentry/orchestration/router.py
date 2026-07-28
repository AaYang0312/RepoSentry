from __future__ import annotations

from typing import Any, Dict, List, Optional

from reposentry.domain.changes import ChangeSet
from reposentry.domain.models import (
    AnalysisRequest,
    ReviewMode,
    RouteDecision,
)


class ComplexityRouter:
    """Explainable deterministic routing before an optional learned router exists.

    When a request carries a server-derived revision pair
    (``base_revision``/``head_revision``), routing is grounded in the change set
    and the user-supplied risk booleans are ignored. Otherwise the router falls
    back to the legacy manual-input behavior.
    """

    # Scoring bands shared by both code paths.
    SINGLE_THRESHOLD = 4
    TEAM_THRESHOLD = 9

    def route(self, request: AnalysisRequest) -> RouteDecision:
        if request.has_revision_pair and request.change_set:
            change_set = _change_set_from_dict(request.change_set)
            decision = self.route_change_set(change_set)
            return RouteDecision(
                mode=decision.mode,
                score=decision.score,
                reasons=decision.reasons + ["routing driven by server-derived change set"],
                selected_agents=decision.selected_agents,
            )
        return self._route_manual(request)

    def route_change_set(self, change_set: ChangeSet) -> RouteDecision:
        """Route purely from a server-derived :class:`ChangeSet`."""

        score, reasons = self._score_from_change_set(change_set)
        return self._select(score, reasons)

    # -- legacy path --------------------------------------------------------

    def _route_manual(self, request: AnalysisRequest) -> RouteDecision:
        score = 0
        reasons: List[str] = []
        changed_file_count = len(request.changed_files)

        if changed_file_count == 0:
            score += 2
            reasons.append("repository-wide request without an explicit diff")
        else:
            file_score = min(changed_file_count, 6)
            score += file_score
            reasons.append("{} changed files".format(changed_file_count))

        if request.changed_lines >= 400:
            score += 4
            reasons.append("large diff ({} lines)".format(request.changed_lines))
        elif request.changed_lines >= 100:
            score += 2
            reasons.append("medium diff ({} lines)".format(request.changed_lines))

        if request.dependency_changed:
            score += 3
            reasons.append("dependency or lockfile changed")
        if request.api_contract_changed:
            score += 3
            reasons.append("API contract changed")
        if request.sensitive_paths:
            score += 3
            reasons.append("sensitive paths changed")

        return self._select(score, reasons or ["small, isolated request"])

    # -- shared scoring -----------------------------------------------------

    @staticmethod
    def _score_from_change_set(change_set: ChangeSet) -> tuple:
        score = 0
        reasons: List[str] = []

        file_count = len(change_set.files)
        if file_count == 0:
            score += 2
            reasons.append("empty diff between revisions")
        else:
            score += min(file_count, 6)
            reasons.append("{} changed files".format(file_count))

        changed_lines = change_set.changed_lines
        if changed_lines >= 400:
            score += 4
            reasons.append("large diff ({} lines)".format(changed_lines))
        elif changed_lines >= 100:
            score += 2
            reasons.append("medium diff ({} lines)".format(changed_lines))

        if change_set.dependency_changed:
            score += 3
            reasons.append("dependency or lockfile changed")
        if change_set.api_contract_changed:
            score += 3
            reasons.append("API contract changed")
        if change_set.sensitive_paths:
            score += 3
            reasons.append("sensitive paths changed ({})".format(
                ", ".join(change_set.sensitive_paths)
            ))
        return score, reasons

    def _select(self, score: int, reasons: List[str]) -> RouteDecision:
        if score < self.SINGLE_THRESHOLD:
            return RouteDecision(
                mode=ReviewMode.SINGLE,
                score=score,
                reasons=reasons,
                selected_agents=["review"],
            )
        if score < self.TEAM_THRESHOLD:
            return RouteDecision(
                mode=ReviewMode.TEAM,
                score=score,
                reasons=reasons,
                selected_agents=["review", "impact"],
            )
        return RouteDecision(
            mode=ReviewMode.SWARM,
            score=score,
            reasons=reasons,
            selected_agents=["review", "impact", "test"],
        )


def _change_set_from_dict(payload: Dict[str, Any]) -> ChangeSet:
    """Reconstruct a ``ChangeSet`` from its serialized form for routing.

    Routing only needs the aggregate counters and the derived flags, so we do
    not resurrect every hunk. The router never inspects ``ChangeSet.files``
    beyond ``len()`` and the path lists.
    """

    from reposentry.domain.changes import ChangedFile, RepositoryRevision

    route_inputs = payload.get("route_inputs", {})
    base_payload = payload.get("base") or {}
    head_payload = payload.get("head") or {}
    files = [
        ChangedFile(path=str(path), status="modified")
        for path in payload.get("changed_files", [])
    ]
    return ChangeSet(
        base=RepositoryRevision(
            repository_path=str(base_payload.get("repository_path", "")),
            sha=str(base_payload.get("sha", "")),
        ),
        head=RepositoryRevision(
            repository_path=str(head_payload.get("repository_path", "")),
            sha=str(head_payload.get("sha", "")),
        ),
        files=files,
        additions=int(payload.get("additions", 0)),
        deletions=int(payload.get("deletions", 0)),
        dependency_changed=bool(route_inputs.get("dependency_changed", False)),
        api_contract_changed=bool(route_inputs.get("api_contract_changed", False)),
        sensitive_paths=list(route_inputs.get("sensitive_paths", [])),
    )
