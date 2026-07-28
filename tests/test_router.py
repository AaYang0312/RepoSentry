import unittest

from reposentry.domain.changes import ChangeSet, ChangedFile, RepositoryRevision
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


class ComplexityRouterChangeSetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.router = ComplexityRouter()
        self.base = RepositoryRevision(repository_path="/repo", sha="b" * 40)
        self.head = RepositoryRevision(repository_path="/repo", sha="a" * 40)

    def _change_set(self, **overrides) -> ChangeSet:
        defaults = dict(
            base=self.base,
            head=self.head,
            files=[ChangedFile(path="src/a.py", status="modified", additions=5, deletions=1)],
            additions=5,
            deletions=1,
        )
        defaults.update(overrides)
        return ChangeSet(**defaults)

    def test_route_change_set_drives_swarm_even_when_request_booleans_false(self) -> None:
        change_set = self._change_set(
            files=[
                ChangedFile(path="requirements.txt", status="modified", additions=50, deletions=10),
                ChangedFile(path="src/api/users.py", status="modified", additions=80, deletions=20),
                ChangedFile(path="src/auth/service.py", status="modified", additions=120, deletions=40),
            ],
            additions=250,
            deletions=70,
            dependency_changed=True,
            api_contract_changed=True,
            sensitive_paths=["src/auth/service.py"],
        )
        # The request's manual booleans are deliberately False to prove the
        # router no longer trusts them when a change set is attached.
        request = AnalysisRequest(
            repository_path="/repo",
            changed_files=["stale.py"],
            additions=0,
            deletions=0,
            dependency_changed=False,
            api_contract_changed=False,
            sensitive_paths=[],
            base_revision=change_set.base.sha,
            head_revision=change_set.head.sha,
            change_set=change_set.to_dict(),
        )
        decision = self.router.route(request)
        self.assertEqual(decision.mode, ReviewMode.SWARM)
        self.assertEqual(decision.selected_agents, ["review", "impact", "test"])
        self.assertIn("routing driven by server-derived change set", decision.reasons)

    def test_route_change_set_small_diff_stays_single(self) -> None:
        change_set = self._change_set()  # one file, 5/1 lines
        decision = self.router.route_change_set(change_set)
        self.assertEqual(decision.mode, ReviewMode.SINGLE)
        self.assertEqual(decision.selected_agents, ["review"])

    def test_route_falls_back_to_manual_when_no_revision_pair(self) -> None:
        # No base/head -> legacy path, identical to the original behavior.
        decision = self.router.route(
            AnalysisRequest(
                repository_path=".",
                changed_files=["src/a.py", "src/b.py", "tests/test_a.py"],
                additions=90,
                deletions=30,
            )
        )
        self.assertEqual(decision.mode, ReviewMode.TEAM)
        self.assertNotIn("routing driven by server-derived change set", decision.reasons)


if __name__ == "__main__":
    unittest.main()

