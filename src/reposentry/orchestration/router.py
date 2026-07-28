from __future__ import annotations

from typing import List

from reposentry.domain.models import (
    AnalysisRequest,
    ReviewMode,
    RouteDecision,
)


class ComplexityRouter:
    """Explainable deterministic routing before an optional learned router exists."""

    def route(self, request: AnalysisRequest) -> RouteDecision:
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

        if score < 4:
            return RouteDecision(
                mode=ReviewMode.SINGLE,
                score=score,
                reasons=reasons or ["small, isolated request"],
                selected_agents=["review"],
            )
        if score < 9:
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

