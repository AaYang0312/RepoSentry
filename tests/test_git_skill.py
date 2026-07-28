import subprocess
import tempfile
import unittest
from pathlib import Path

from reposentry.skills.git import DiffParser, GitClient, GitError, validate_ref


FIXTURES = Path(__file__).parent / "fixtures" / "diffs"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class RefValidationTests(unittest.TestCase):
    def test_accepts_common_revision_syntax(self) -> None:
        for ref in ("HEAD", "HEAD~1", "HEAD^", "main", "feature/branch",
                    "abc1234", "v1.2.3", "refs/tags/v1", "a" * 40, "main:feature"):
            self.assertEqual(validate_ref(ref), ref)

    def test_rejects_leading_dash(self) -> None:
        with self.assertRaises(GitError):
            validate_ref("-")

    def test_rejects_shell_metacharacters(self) -> None:
        # Refs are sandboxed inside .git/ by git itself, so we only need to
        # block shell/flag injection vectors here. Path traversal is enforced
        # at the diff-path level (ChangeSet.is_within_root), not on refs.
        for bad in (";rm -rf /", "$(whoami)", "a b", "a;b", "a|b",
                    "ref with space", "", None):  # type: ignore[arg-type]
            with self.assertRaises(GitError):
                validate_ref(bad)  # type: ignore[arg-type]


class GitClientConstructionTests(unittest.TestCase):
    def test_rejects_missing_repository(self) -> None:
        with self.assertRaises(GitError):
            GitClient(Path("/nonexistent/path/that/does/not/exist"))


class DiffParserFixtureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = DiffParser()
        self.base_sha = "b" * 40
        self.head_sha = "a" * 40

    def _rev(self, sha: str):
        from reposentry.domain.changes import RepositoryRevision

        return RepositoryRevision(repository_path="/repo", sha=sha)

    def test_parse_sample_diff(self) -> None:
        numstat = (
            "2\t1\trequirements.txt\n"
            "3\t1\tsrc/api/users.py\n"
            "3\t0\tsrc/auth/service.py\n"
        )
        name_status = (
            "M\trequirements.txt\n"
            "M\tsrc/api/users.py\n"
            "M\tsrc/auth/service.py\n"
        )
        change_set = self.parser.parse(
            self._rev(self.base_sha),
            self._rev(self.head_sha),
            numstat,
            name_status,
            _fixture("sample.diff"),
        )
        self.assertEqual(change_set.changed_files, [
            "requirements.txt",
            "src/api/users.py",
            "src/auth/service.py",
        ])
        self.assertEqual(change_set.additions, 8)
        self.assertEqual(change_set.deletions, 2)
        self.assertTrue(change_set.dependency_changed)
        self.assertTrue(change_set.api_contract_changed)
        self.assertEqual(change_set.sensitive_paths, ["src/auth/service.py"])

        # Hunks parsed for one of the files.
        users = next(f for f in change_set.files if f.path == "src/api/users.py")
        self.assertEqual(len(users.hunks), 1)
        self.assertEqual(users.hunks[0].new_start, 10)
        self.assertEqual(users.hunks[0].new_len, 7)
        self.assertIn("raise ValueError", users.hunks[0].body)

    def test_parse_renames_and_binary(self) -> None:
        numstat = "-\t-\tbin.dat\n1\t0\tnewname.txt\n"
        name_status = "M\tbin.dat\nR066\toldname.txt\tnewname.txt\n"
        change_set = self.parser.parse(
            self._rev(self.base_sha),
            self._rev(self.head_sha),
            numstat,
            name_status,
            _fixture("renames.diff"),
        )
        by_path = {f.path: f for f in change_set.files}
        self.assertEqual(by_path["newname.txt"].status, "renamed")
        self.assertEqual(by_path["newname.txt"].additions, 1)
        # Binary file: counts collapsed to zero, still listed.
        self.assertEqual(by_path["bin.dat"].additions, 0)
        self.assertEqual(by_path["bin.dat"].deletions, 0)

    def test_parse_empty_diff(self) -> None:
        change_set = self.parser.parse(
            self._rev(self.base_sha),
            self._rev(self.head_sha),
            "",
            "",
            "",
        )
        self.assertEqual(change_set.files, [])
        self.assertEqual(change_set.additions, 0)
        self.assertEqual(change_set.deletions, 0)
        self.assertFalse(change_set.dependency_changed)

    def test_parse_rejects_path_escaping_root(self) -> None:
        # The parser is fail-closed: a path escaping the repository root raises
        # rather than silently dropping the file.
        with self.assertRaises(GitError):
            self.parser.parse(
                self._rev(self.base_sha),
                self._rev(self.head_sha),
                "1\t0\t../escape.py",
                "M\t../escape.py",
                "",
            )


class GitClientIntegrationTests(unittest.TestCase):
    """Exercises the real git binary in a throwaway repository."""

    @staticmethod
    def _git(root: Path, *args: str) -> str:
        completed = subprocess.run(
            ["git", "-C", str(root), *args],
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout.strip()

    @staticmethod
    def _make_repo() -> Path:
        root = Path(tempfile.mkdtemp(prefix="reposentry-git-"))
        GitClientIntegrationTests._git(root, "init", "-q")
        GitClientIntegrationTests._git(root, "config", "user.email", "t@t.test")
        GitClientIntegrationTests._git(root, "config", "user.name", "Test")
        return root

    def test_diff_round_trip_against_real_git(self) -> None:
        root = self._make_repo()
        (root / "README.md").write_text("# Repo\n\nbody.\n", encoding="utf-8")
        (root / "requirements.txt").write_text("a==1\n", encoding="utf-8")
        self._git(root, "add", ".")
        self._git(root, "commit", "-qm", "base")
        base = self._git(root, "rev-parse", "HEAD")

        (root / "README.md").write_text("# Repo\n\nbody.\nmore.\n", encoding="utf-8")
        (root / "requirements.txt").write_text("a==1\nb==2\n", encoding="utf-8")
        self._git(root, "add", ".")
        self._git(root, "commit", "-qm", "head")
        head = self._git(root, "rev-parse", "HEAD")

        client = GitClient(root)
        change_set = client.diff(base, head)
        self.assertEqual(change_set.base.sha, base)
        self.assertEqual(change_set.head.sha, head)
        self.assertEqual(sorted(change_set.changed_files), ["README.md", "requirements.txt"])
        self.assertTrue(change_set.dependency_changed)
        self.assertGreater(change_set.additions, 0)

    def test_resolve_revision_accepts_relative_syntax(self) -> None:
        root = self._make_repo()
        (root / "f.txt").write_text("x\n", encoding="utf-8")
        self._git(root, "add", ".")
        self._git(root, "commit", "-qm", "one")
        (root / "f.txt").write_text("x\ny\n", encoding="utf-8")
        self._git(root, "add", ".")
        self._git(root, "commit", "-qm", "two")
        client = GitClient(root)
        head_sha = client.resolve_revision("HEAD")
        parent_sha = client.resolve_revision("HEAD~1")
        self.assertEqual(len(head_sha), 40)
        self.assertEqual(len(parent_sha), 40)
        self.assertNotEqual(head_sha, parent_sha)

    def test_resolve_revision_raises_on_unknown_ref(self) -> None:
        root = self._make_repo()
        client = GitClient(root)
        with self.assertRaises(GitError):
            client.resolve_revision("definitely-not-a-ref-xyz")


if __name__ == "__main__":
    unittest.main()
