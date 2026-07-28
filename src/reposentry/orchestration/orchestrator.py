from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from uuid import uuid4

from reposentry.domain.models import (
    AgentRunResult,
    AnalysisReport,
    AnalysisRequest,
    Evidence,
    Finding,
    RunStatus,
    Severity,
)
from reposentry.orchestration.agents import AGENT_SPECS
from reposentry.orchestration.router import ComplexityRouter
from reposentry.orchestration.verification import EvidenceGate
from reposentry.runtime.agent import AgentRuntime, AgentSpec
from reposentry.runtime.context import ArtifactStore


RuntimeFactory = Callable[[AgentSpec], AgentRuntime]


class ReviewOrchestrator:
    """Fan-out specialist agents, apply an evidence gate, then verify and merge."""

    def __init__(
        self,
        runtime_factory: RuntimeFactory,
        repository_root: Path,
        router: Optional[ComplexityRouter] = None,
        max_parallel_agents: int = 3,
    ) -> None:
        self._runtime_factory = runtime_factory
        self._router = router or ComplexityRouter()
        self._gate = EvidenceGate(repository_root)
        self._parallelism = max(1, max_parallel_agents)
        self._artifacts = ArtifactStore()

    @property
    def artifacts(self) -> ArtifactStore:
        return self._artifacts

    async def analyze(
        self,
        request: AnalysisRequest,
        task_id: Optional[str] = None,
    ) -> AnalysisReport:
        current_task_id = task_id or str(uuid4())
        route = self._router.route(request)
        await self._artifacts.put("request", request.to_dict())
        await self._artifacts.put("route", route.to_dict())

        semaphore = asyncio.Semaphore(self._parallelism)

        async def run_specialist(agent_key: str) -> AgentRunResult:
            async with semaphore:
                spec = AGENT_SPECS[agent_key]
                runtime = self._runtime_factory(spec)
                task = self._specialist_task(request, route.to_dict(), agent_key)
                result = await runtime.run(current_task_id, spec, task)
                await self._artifacts.append("agent_results", result.to_dict())
                return result

        results = await asyncio.gather(
            *(run_specialist(agent) for agent in route.selected_agents)
        )

        candidate_findings: List[Finding] = []
        summaries: List[str] = []
        rejected: List[Dict[str, Any]] = []
        for result in results:
            findings, summary, parse_rejections = self._parse_agent_output(result)
            candidate_findings.extend(findings)
            if summary:
                summaries.append(summary)
            rejected.extend(parse_rejections)

        grounded, gate_rejections = self._gate.filter(candidate_findings)
        rejected.extend(gate_rejections)
        await self._artifacts.put(
            "grounded_findings",
            [item.to_dict() for item in grounded],
        )

        accepted, verifier_rejections, verifier_result = await self._verify(
            current_task_id,
            grounded,
        )
        rejected.extend(verifier_rejections)
        if verifier_result is not None:
            results.append(verifier_result)

        summary = (
            " | ".join(summaries)
            if summaries
            else "No specialist completed with a structured summary."
        )
        return AnalysisReport(
            task_id=current_task_id,
            route=route,
            summary=summary,
            findings=accepted,
            agent_results=results,
            rejected_findings=rejected,
        )

    async def _verify(
        self,
        task_id: str,
        findings: List[Finding],
    ) -> Tuple[List[Finding], List[Dict[str, Any]], Optional[AgentRunResult]]:
        if not findings:
            return [], [], None

        spec = AGENT_SPECS["verifier"]
        runtime = self._runtime_factory(spec)
        payload = json.dumps(
            {"candidate_findings": [item.to_dict() for item in findings]},
            ensure_ascii=False,
        )
        result = await runtime.run(task_id, spec, payload)
        if result.status != RunStatus.COMPLETED:
            return findings, [
                {
                    "finding_id": "",
                    "agent": spec.name,
                    "reason": "verifier unavailable; deterministic gate used",
                }
            ], result

        try:
            parsed = json.loads(result.output)
            accepted_ids = set(parsed.get("accepted_finding_ids", []))
            model_rejected = parsed.get("rejected", [])
            if not isinstance(model_rejected, list):
                model_rejected = []
        except (json.JSONDecodeError, AttributeError, TypeError):
            return findings, [
                {
                    "finding_id": "",
                    "agent": spec.name,
                    "reason": "verifier output was invalid; deterministic gate used",
                }
            ], result

        if not accepted_ids and not model_rejected:
            return findings, [], result

        accepted = [item for item in findings if item.finding_id in accepted_ids]
        rejected = [
            {
                "finding_id": item.finding_id,
                "agent": item.agent,
                "reason": "rejected by VerifierAgent",
            }
            for item in findings
            if item.finding_id not in accepted_ids
        ]
        rejected.extend(
            item for item in model_rejected if isinstance(item, dict)
        )
        return accepted, rejected, result

    @staticmethod
    def _specialist_task(
        request: AnalysisRequest,
        route: Dict[str, Any],
        agent_key: str,
    ) -> str:
        return json.dumps(
            {
                "objective": "Review repository changes with code evidence.",
                "specialist": agent_key,
                "request": request.to_dict(),
                "route": route,
                "output_contract": {
                    "summary": "string",
                    "findings": [
                        {
                            "category": "string",
                            "summary": "string",
                            "severity": "info|low|medium|high|critical",
                            "confidence": "number between 0 and 1",
                            "recommendation": "string",
                            "evidence": [
                                {
                                    "path": "repository-relative path",
                                    "line_start": "positive integer",
                                    "line_end": "optional positive integer",
                                    "symbol": "optional string",
                                }
                            ],
                        }
                    ],
                },
            },
            ensure_ascii=False,
        )

    @staticmethod
    def _parse_agent_output(
        result: AgentRunResult,
    ) -> Tuple[List[Finding], str, List[Dict[str, Any]]]:
        if result.status != RunStatus.COMPLETED:
            return [], "", [
                {
                    "finding_id": "",
                    "agent": result.agent,
                    "reason": result.error or "agent did not complete",
                }
            ]
        try:
            payload = json.loads(result.output)
        except json.JSONDecodeError:
            return [], "", [
                {
                    "finding_id": "",
                    "agent": result.agent,
                    "reason": "agent output is not valid JSON",
                }
            ]

        if not isinstance(payload, dict):
            return [], "", [
                {
                    "finding_id": "",
                    "agent": result.agent,
                    "reason": "agent output must be a JSON object",
                }
            ]

        findings: List[Finding] = []
        rejected: List[Dict[str, Any]] = []
        raw_findings = payload.get("findings", [])
        if not isinstance(raw_findings, list):
            raw_findings = []
        for raw in raw_findings:
            try:
                evidence = [
                    Evidence(
                        path=item["path"],
                        line_start=int(item["line_start"]),
                        line_end=(
                            int(item["line_end"])
                            if item.get("line_end") is not None
                            else None
                        ),
                        symbol=item.get("symbol"),
                        excerpt=item.get("excerpt"),
                    )
                    for item in raw.get("evidence", [])
                ]
                findings.append(
                    Finding(
                        category=str(raw["category"]),
                        summary=str(raw["summary"]),
                        severity=Severity(str(raw["severity"]).lower()),
                        confidence=float(raw["confidence"]),
                        evidence=evidence,
                        agent=result.agent,
                        recommendation=raw.get("recommendation"),
                    )
                )
            except (KeyError, TypeError, ValueError, AttributeError) as exc:
                rejected.append(
                    {
                        "finding_id": "",
                        "agent": result.agent,
                        "reason": "invalid finding schema: {}".format(exc),
                    }
                )
        return findings, str(payload.get("summary", "")), rejected

