import tempfile
import unittest
from pathlib import Path

from reposentry.runtime.tools import ToolExecutionError
from reposentry.skills.repository import RepositoryToolkit


class RepositoryToolkitTests(unittest.TestCase):
    def test_tools_are_bounded_to_repository_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "README.md").write_text(
                "RepoSentry\nagent runtime\n",
                encoding="utf-8",
            )
            toolkit = RepositoryToolkit(root)

            listing = toolkit.list_files()
            self.assertEqual(listing["files"], ["README.md"])

            search = toolkit.search_code("agent")
            self.assertEqual(search["matches"][0]["line"], 2)

            with self.assertRaises(ToolExecutionError):
                toolkit.read_file("../outside.txt")


if __name__ == "__main__":
    unittest.main()

