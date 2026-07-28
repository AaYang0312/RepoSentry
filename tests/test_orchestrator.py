import asyncio
import tempfile
import unittest
from pathlib import Path

from reposentry.adapters.demo import DemoModelClient
from reposentry.domain.models import AnalysisRequest, ReviewMode
from reposentry.orchestration.orchestrator import ReviewOrchestrator
from reposentry.runtime.agent import AgentRuntime
from reposentry.runtime.events import EventBus
from reposentry.skills.repository import RepositoryToolkit


class OrchestratorTests(unittest.TestCase):
    @staticmethod
    async def _run_analysis(root: Path, request: AnalysisRequest):
        toolkit = RepositoryToolkit(root)
        registry = toolkit.registry()
        events = EventBus()

        def runtime_factory(spec):
            return AgentRuntime(
                model=DemoModelClient(),
                tools=registry,
                event_bus=events,
            )

        orchestrator = ReviewOrchestrator(
            runtime_factory=runtime_factory,
            repository_root=root,
        )
        report = await orchestrator.analyze(request, task_id="demo-task")
        return report, await events.snapshot()

    def test_demo_provider_runs_end_to_end(self) -> None:
        async def scenario(root: Path):
            return await self._run_analysis(
                root,
                AnalysisRequest(
                    repository_path=str(root),
                    changed_files=["README.md"],
                    additions=4,
                ),
            )

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "README.md").write_text(
                "# RepoSentry\n\nDemo repository.\n",
                encoding="utf-8",
            )
            report, events = asyncio.run(scenario(root))

        self.assertEqual(report.task_id, "demo-task")
        self.assertEqual(report.route.mode, ReviewMode.SINGLE)
        self.assertEqual(len(report.findings), 1)
        self.assertEqual(report.findings[0].evidence[0].path, "README.md")
        self.assertEqual(
            [result.agent for result in report.agent_results],
            ["ReviewAgent", "VerifierAgent"],
        )
        self.assertGreater(len(events), 0)

    def test_high_risk_request_fans_out_and_deduplicates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "README.md").write_text("# RepoSentry\n", encoding="utf-8")
            report, _ = asyncio.run(
                self._run_analysis(
                    root,
                    AnalysisRequest(
                        repository_path=str(root),
                        changed_files=["README.md", "pyproject.toml"],
                        additions=500,
                        dependency_changed=True,
                        api_contract_changed=True,
                        sensitive_paths=["src/auth.py"],
                    ),
                )
            )

        self.assertEqual(report.route.mode, ReviewMode.SWARM)
        self.assertEqual(
            [result.agent for result in report.agent_results],
            ["ReviewAgent", "ImpactAgent", "TestAgent", "VerifierAgent"],
        )
        self.assertEqual(len(report.findings), 1)
        self.assertEqual(
            [item["reason"] for item in report.rejected_findings],
            ["duplicate finding", "duplicate finding"],
        )


if __name__ == "__main__":
    unittest.main()
