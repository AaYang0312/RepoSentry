from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from reposentry.domain.models import Finding


class EvidenceGate:
    """Deterministic checks run before the LLM verifier."""

    def __init__(self, repository_root: Path) -> None:
        self._root = repository_root.expanduser().resolve()

    def filter(
        self,
        findings: List[Finding],
    ) -> Tuple[List[Finding], List[Dict[str, str]]]:
        accepted: List[Finding] = []
        rejected: List[Dict[str, str]] = []
        seen: Set[Tuple[str, str, str, int]] = set()

        for finding in findings:
            reason = self._rejection_reason(finding)
            evidence = finding.evidence[0] if finding.evidence else None
            dedupe_key = (
                finding.category,
                finding.summary.strip().lower(),
                evidence.path if evidence else "",
                evidence.line_start if evidence else 0,
            )
            if reason is None and dedupe_key in seen:
                reason = "duplicate finding"
            if reason is not None:
                rejected.append(
                    {
                        "finding_id": finding.finding_id,
                        "agent": finding.agent,
                        "reason": reason,
                    }
                )
                continue
            seen.add(dedupe_key)
            accepted.append(finding)
        return accepted, rejected

    def _rejection_reason(self, finding: Finding) -> Optional[str]:
        if not 0.0 <= finding.confidence <= 1.0:
            return "confidence must be between 0 and 1"
        if not finding.evidence:
            return "finding has no evidence"
        for evidence in finding.evidence:
            if not evidence.is_well_formed():
                return "evidence path or line range is malformed"
            target = (self._root / evidence.path).resolve()
            try:
                target.relative_to(self._root)
            except ValueError:
                return "evidence escapes repository root"
            if not target.is_file():
                return "evidence file does not exist"
            try:
                with target.open(
                    "r",
                    encoding="utf-8",
                    errors="replace",
                ) as handle:
                    line_count = sum(1 for _ in handle)
            except OSError:
                return "evidence file cannot be read"
            if evidence.line_start > max(1, line_count):
                return "evidence line is outside the file"
        return None
