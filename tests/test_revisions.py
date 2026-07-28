import subprocess
import tempfile
import unittest
from pathlib import Path

from reposentry.domain.changes import ChangeSet
from reposentry.domain.models import AnalysisRequest
from reposentry.services.revisions import RevisionService, attach_change_set
from reposentry.skills.git import GitError


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _make_repo() -> Path:
    root = Path(tempfile.mkdtemp(prefix="reposentry-rev-"))
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@t.test")
    _git(root, "config", "user.name", "Test")
    return root


class RevisionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = _make_repo()
        (self.root / "src" / "auth").mkdir(parents=True)
        (self.root / "src" / "auth" / "service.py").write_text(
            "def authenticate():\n    pass\n", encoding="utf-8"
        )
        (self.root / "requirements.txt").write_text("a==1\n", encoding="utf-8")
        _git(self.root, "add", ".")
        _git(self.root, "commit", "-qm", "base")
        self.base = _git(self.root, "rev-parse", "HEAD")

        (self.root / "src" / "auth" / "service.py").write_text(
            "def authenticate():\n    return True\n\ndef issue():\n    pass\n",
            encoding="utf-8",
        )
        (self.root / "requirements.txt").write_text("a==1\nb==2\n", encoding="utf-8")
        _git(self.root, "add", ".")
        _git(self.root, "commit", "-qm", "head")
        self.head = _git(self.root, "rev-parse", "HEAD")

    def test_parse_builds_change_set_from_git(self) -> None:
        service = RevisionService()
        change_set = service.parse(
            base_ref=self.base,
            head_ref=self.head,
            repository_path=str(self.root),
        )
        self.assertIsInstance(change_set, ChangeSet)
        self.assertEqual(change_set.base.sha, self.base)
        self.assertEqual(change_set.head.sha, self.head)
        self.assertIn("requirements.txt", change_set.changed_files)
        self.assertIn("src/auth/service.py", change_set.changed_files)
        self.assertTrue(change_set.dependency_changed)
        self.assertEqual(change_set.sensitive_paths, ["src/auth/service.py"])
        self.assertGreater(change_set.additions, 0)

    def test_parse_accepts_relative_revision_syntax(self) -> None:
        service = RevisionService()
        change_set = service.parse(
            base_ref="HEAD~1",
            head_ref="HEAD",
            repository_path=str(self.root),
        )
        self.assertEqual(change_set.base.sha, self.base)
        self.assertEqual(change_set.head.sha, self.head)

    def test_parse_rejects_unknown_ref(self) -> None:
        service = RevisionService()
        with self.assertRaises(GitError):
            service.parse(
                base_ref="no-such-ref",
                head_ref=self.head,
                repository_path=str(self.root),
            )

    def test_build_request_projects_change_set_onto_request(self) -> None:
        service = RevisionService()
        change_set = service.parse(
            base_ref=self.base,
            head_ref=self.head,
            repository_path=str(self.root),
        )
        request = service.build_request(
            repository_path=str(self.root),
            change_set=change_set,
            pr_number=42,
        )
        self.assertTrue(request.has_revision_pair)
        self.assertEqual(request.base_revision, self.base)
        self.assertEqual(request.head_revision, self.head)
        self.assertEqual(request.pr_number, 42)
        self.assertEqual(request.changed_files, change_set.changed_files)
        self.assertEqual(request.additions, change_set.additions)
        self.assertTrue(request.dependency_changed)
        self.assertEqual(request.sensitive_paths, ["src/auth/service.py"])
        self.assertIsNotNone(request.change_set)
        self.assertIn("route_inputs", request.change_set)


class AttachChangeSetTests(unittest.TestCase):
    def test_attach_overwrites_request_fields_from_change_set(self) -> None:
        request = AnalysisRequest(
            repository_path="/repo",
            changed_files=["stale.py"],
            additions=1,
            deletions=1,
            dependency_changed=False,
            sensitive_paths=["stale.py"],
            base_revision="b" * 40,
            head_revision="a" * 40,
        )
        change_set_dict = {
            "base": {"repository_path": "/repo", "sha": "b" * 40},
            "head": {"repository_path": "/repo", "sha": "a" * 40},
            "changed_files": ["src/auth/service.py"],
            "additions": 5,
            "deletions": 0,
            "route_inputs": {
                "dependency_changed": True,
                "api_contract_changed": False,
                "sensitive_paths": ["src/auth/service.py"],
            },
        }
        attached = attach_change_set(request, change_set_dict)
        self.assertEqual(attached.changed_files, ["src/auth/service.py"])
        self.assertEqual(attached.additions, 5)
        self.assertTrue(attached.dependency_changed)
        self.assertEqual(attached.sensitive_paths, ["src/auth/service.py"])
        self.assertEqual(attached.change_set, change_set_dict)
        # Original is untouched (frozen dataclass).
        self.assertEqual(request.changed_files, ["stale.py"])


if __name__ == "__main__":
    unittest.main()
