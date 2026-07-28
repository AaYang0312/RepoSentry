import unittest

from reposentry.domain.changes import (
    ChangeSet,
    ChangedFile,
    DiffEvidence,
    DiffHunk,
    RepositoryRevision,
    is_api_contract_path,
    is_dependency_path,
    is_sensitive_path,
)


class PathDetectorTests(unittest.TestCase):
    def test_dependency_detector_matches_manifests_and_lockfiles(self) -> None:
        for path in (
            "requirements.txt",
            "requirements-dev.txt",
            "requirements.lock",
            "constraints.txt",
            "pyproject.toml",
            "package-lock.json",
            "go.sum",
            "Cargo.lock",
            "uv.lock",
            "subdir/yarn.lock",
        ):
            self.assertTrue(is_dependency_path(path), path)

    def test_dependency_detector_rejects_unrelated_files(self) -> None:
        for path in ("src/main.py", "README.txt", "notes.txt", "", "config.toml"):
            self.assertFalse(is_dependency_path(path), path)

    def test_api_contract_detector_matches_routes_and_api_dirs(self) -> None:
        for path in (
            "src/api/users.py",
            "src/endpoints/routes.py",
            "schemas.py",
            "app/api/schemas.py",
            "docs/openapi.yaml",
        ):
            self.assertTrue(is_api_contract_path(path), path)

    def test_api_contract_detector_rejects_unrelated(self) -> None:
        for path in ("src/models.py", "tests/test_api.py", "", "utils.py"):
            self.assertFalse(is_api_contract_path(path), path)

    def test_sensitive_detector_matches_security_areas(self) -> None:
        for path in (
            "src/auth/service.py",
            "pkg/security/check.go",
            "lib/crypto/sign.py",
            "config/secrets.yaml",
            ".github/workflows/deploy.yml",
            "src/settings.py",
            "Dockerfile",
        ):
            self.assertTrue(is_sensitive_path(path), path)

    def test_sensitive_detector_rejects_unrelated(self) -> None:
        for path in ("src/users.py", "README.md", "", "tests/test_users.py"):
            self.assertFalse(is_sensitive_path(path), path)


class ChangeSetValueObjectTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base = RepositoryRevision(repository_path="/repo", sha="b" * 40)
        self.head = RepositoryRevision(repository_path="/repo", sha="a" * 40)
        self.hunk = DiffHunk(old_start=1, old_len=3, new_start=1, new_len=4, body=" x")

    def test_changed_file_changed_lines_and_dict(self) -> None:
        file = ChangedFile(
            path="src/a.py", status="modified", additions=5, deletions=2, hunks=[self.hunk]
        )
        self.assertEqual(file.changed_lines, 7)
        payload = file.to_dict()
        self.assertEqual(payload["status"], "modified")
        self.assertEqual(len(payload["hunks"]), 1)
        self.assertEqual(payload["hunks"][0]["new_start"], 1)

    def test_change_set_aggregates_counts_and_derives_flags(self) -> None:
        # The ChangeSet does not auto-sum file counts; totals are supplied by
        # the Git parser. Here we assert the aggregate surfaces them faithfully.
        change_set = ChangeSet(
            base=self.base,
            head=self.head,
            files=[
                ChangedFile(path="requirements.txt", status="modified", additions=2, deletions=1),
                ChangedFile(path="src/auth/service.py", status="modified", additions=4, deletions=0),
                ChangedFile(path="src/api/users.py", status="modified", additions=3, deletions=1),
            ],
            additions=9,
            deletions=2,
        )
        self.assertEqual(change_set.changed_files, [
            "requirements.txt",
            "src/auth/service.py",
            "src/api/users.py",
        ])
        self.assertEqual(change_set.additions, 9)
        self.assertEqual(change_set.deletions, 2)
        self.assertEqual(change_set.changed_lines, 11)
        # The aggregate itself does not set derived flags; the Git parser does.
        # But to_dict surfaces route_inputs verbatim.
        payload = change_set.to_dict()
        self.assertIn("route_inputs", payload)
        self.assertEqual(payload["route_inputs"]["sensitive_paths"], [])

    def test_to_dict_includes_route_inputs(self) -> None:
        change_set = ChangeSet(
            base=self.base,
            head=self.head,
            files=[ChangedFile(path="x.py", status="modified", additions=1, deletions=0)],
            additions=1,
            deletions=0,
            dependency_changed=True,
            api_contract_changed=True,
            sensitive_paths=["src/auth/service.py"],
        )
        payload = change_set.to_dict()
        self.assertTrue(payload["route_inputs"]["dependency_changed"])
        self.assertTrue(payload["route_inputs"]["api_contract_changed"])
        self.assertEqual(payload["route_inputs"]["sensitive_paths"], ["src/auth/service.py"])
        self.assertEqual(payload["base"]["sha"], "b" * 40)
        self.assertEqual(payload["head"]["sha"], "a" * 40)

    def test_diff_evidence_dict(self) -> None:
        evidence = DiffEvidence(
            base=self.base,
            head=self.head,
            path="src/a.py",
            line_start=1,
            line_end=4,
            hunks=[self.hunk],
        )
        payload = evidence.to_dict()
        self.assertEqual(payload["path"], "src/a.py")
        self.assertEqual(payload["line_start"], 1)
        self.assertEqual(payload["base"]["sha"], "b" * 40)


class IsWithinRootTests(unittest.TestCase):
    def test_rejects_absolute_and_parent_paths(self) -> None:
        self.assertFalse(ChangeSet.is_within_root("/etc/passwd"))
        self.assertFalse(ChangeSet.is_within_root("../escape.py"))
        self.assertFalse(ChangeSet.is_within_root("a/../../escape.py"))
        self.assertFalse(ChangeSet.is_within_root(""))

    def test_accepts_repository_relative_paths(self) -> None:
        self.assertTrue(ChangeSet.is_within_root("src/a.py"))
        self.assertTrue(ChangeSet.is_within_root("a/b/c.py"))


if __name__ == "__main__":
    unittest.main()
