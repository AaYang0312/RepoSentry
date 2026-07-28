import unittest

from reposentry.domain.models import AnalysisRequest, ReviewMode
from reposentry.orchestration.router import ComplexityRouter


class ComplexityRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.router = ComplexityRouter()

    def test_small_change_uses_single_agent(self) -> None:
        decision = self.router.route(
            AnalysisRequest(
                repository_path=".",
                changed_files=["src/user.py"],
                additions=10,
                deletions=2,
            )
        )
        self.assertEqual(decision.mode, ReviewMode.SINGLE)
        self.assertEqual(decision.selected_agents, ["review"])

    def test_medium_change_uses_team(self) -> None:
        decision = self.router.route(
            AnalysisRequest(
                repository_path=".",
                changed_files=["src/a.py", "src/b.py", "tests/test_a.py"],
                additions=90,
                deletions=30,
            )
        )
        self.assertEqual(decision.mode, ReviewMode.TEAM)
        self.assertEqual(decision.selected_agents, ["review", "impact"])

    def test_high_risk_change_uses_swarm(self) -> None:
        decision = self.router.route(
            AnalysisRequest(
                repository_path=".",
                changed_files=["requirements.txt", "src/auth.py"],
                additions=350,
                deletions=100,
                dependency_changed=True,
                api_contract_changed=True,
                sensitive_paths=["src/auth.py"],
            )
        )
        self.assertEqual(decision.mode, ReviewMode.SWARM)
        self.assertEqual(
            decision.selected_agents,
            ["review", "impact", "test"],
        )


if __name__ == "__main__":
    unittest.main()

