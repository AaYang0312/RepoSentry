from __future__ import annotations

import fnmatch
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from reposentry.runtime.tools import ToolDefinition, ToolExecutionError, ToolRegistry


DEFAULT_EXCLUDED_DIRECTORIES = {
    ".git",
    ".idea",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "vendor",
}


class RepositoryToolkit:
    """Filesystem and Git skills constrained to one repository root."""

    def __init__(self, repository_root: Path) -> None:
        root = repository_root.expanduser().resolve()
        if not root.is_dir():
            raise ValueError("repository path does not exist: {}".format(root))
        self._root = root

    @property
    def root(self) -> Path:
        return self._root

    def registry(self) -> ToolRegistry:
        registry = ToolRegistry()
        registry.register(
            ToolDefinition(
                name="list_files",
                description="List repository files without entering ignored build directories.",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "default": "."},
                        "pattern": {"type": "string"},
                        "max_files": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 500,
                            "default": 100,
                        },
                    },
                    "additionalProperties": False,
                },
                handler=self.list_files,
            )
        )
        registry.register(
            ToolDefinition(
                name="read_file",
                description="Read a bounded line range from a UTF-8 repository file.",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "line_start": {
                            "type": "integer",
                            "minimum": 1,
                            "default": 1,
                        },
                        "line_end": {
                            "type": "integer",
                            "minimum": 1,
                            "default": 200,
                        },
                    },
                    "required": ["path"],
                    "additionalProperties": False,
                },
                handler=self.read_file,
            )
        )
        registry.register(
            ToolDefinition(
                name="search_code",
                description="Search text files and return file and line evidence.",
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "pattern": {"type": "string"},
                        "max_results": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 200,
                            "default": 50,
                        },
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
                handler=self.search_code,
            )
        )
        registry.register(
            ToolDefinition(
                name="git_diff",
                description="Read a bounded unified diff using safe Git subprocess arguments.",
                parameters={
                    "type": "object",
                    "properties": {
                        "base_ref": {"type": "string", "default": "HEAD~1"},
                        "head_ref": {"type": "string", "default": "HEAD"},
                        "max_chars": {
                            "type": "integer",
                            "minimum": 1000,
                            "maximum": 100000,
                            "default": 30000,
                        },
                    },
                    "additionalProperties": False,
                },
                handler=self.git_diff,
            )
        )
        return registry

    def list_files(
        self,
        path: str = ".",
        pattern: Optional[str] = None,
        max_files: int = 100,
    ) -> Dict[str, Any]:
        target = self._safe_path(path)
        if not target.is_dir():
            raise ToolExecutionError("not a directory: {}".format(path))

        files: List[str] = []
        for current_root, directories, names in os.walk(str(target)):
            directories[:] = sorted(
                item
                for item in directories
                if item not in DEFAULT_EXCLUDED_DIRECTORIES
            )
            for name in sorted(names):
                candidate = Path(current_root) / name
                relative = candidate.relative_to(self._root).as_posix()
                if pattern and not fnmatch.fnmatch(relative, pattern):
                    continue
                files.append(relative)
                if len(files) >= min(max_files, 500):
                    return {"files": files, "truncated": True}
        return {"files": files, "truncated": False}

    def read_file(
        self,
        path: str,
        line_start: int = 1,
        line_end: int = 200,
    ) -> Dict[str, Any]:
        target = self._safe_path(path)
        if not target.is_file():
            raise ToolExecutionError("not a file: {}".format(path))
        if line_end < line_start:
            raise ToolExecutionError("line_end must be greater than or equal to line_start")
        if line_end - line_start > 500:
            raise ToolExecutionError("a read_file call may return at most 500 lines")

        try:
            lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            raise ToolExecutionError(str(exc)) from exc

        selected = lines[line_start - 1 : line_end]
        return {
            "path": target.relative_to(self._root).as_posix(),
            "line_start": line_start,
            "line_end": line_start + max(0, len(selected) - 1),
            "content": "\n".join(
                "{:>6}  {}".format(index, value)
                for index, value in enumerate(selected, start=line_start)
            ),
            "truncated": line_end < len(lines),
        }

    def search_code(
        self,
        query: str,
        pattern: Optional[str] = None,
        max_results: int = 50,
    ) -> Dict[str, Any]:
        if not query:
            raise ToolExecutionError("query cannot be empty")
        matches: List[Dict[str, Any]] = []
        listing = self.list_files(pattern=pattern, max_files=500)
        for relative in listing["files"]:
            target = self._safe_path(relative)
            if target.stat().st_size > 2_000_000:
                continue
            try:
                lines = target.read_text(encoding="utf-8", errors="strict").splitlines()
            except (OSError, UnicodeDecodeError):
                continue
            for index, line in enumerate(lines, start=1):
                if query.lower() not in line.lower():
                    continue
                matches.append(
                    {
                        "path": relative,
                        "line": index,
                        "excerpt": line.strip()[:500],
                    }
                )
                if len(matches) >= min(max_results, 200):
                    return {"matches": matches, "truncated": True}
        return {"matches": matches, "truncated": False}

    def git_diff(
        self,
        base_ref: str = "HEAD~1",
        head_ref: str = "HEAD",
        max_chars: int = 30_000,
    ) -> Dict[str, Any]:
        for value in (base_ref, head_ref):
            if value.startswith("-"):
                raise ToolExecutionError("Git refs cannot begin with '-'")
        command = [
            "git",
            "-C",
            str(self._root),
            "diff",
            "--no-ext-diff",
            "--unified=3",
            base_ref,
            head_ref,
            "--",
        ]
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ToolExecutionError("git diff failed: {}".format(exc)) from exc
        if completed.returncode != 0:
            raise ToolExecutionError(completed.stderr.strip() or "git diff failed")
        diff = completed.stdout
        limit = min(max(max_chars, 1000), 100_000)
        return {
            "base_ref": base_ref,
            "head_ref": head_ref,
            "diff": diff[:limit],
            "truncated": len(diff) > limit,
        }

    def _safe_path(self, relative_path: str) -> Path:
        candidate = (self._root / relative_path).resolve()
        try:
            candidate.relative_to(self._root)
        except ValueError as exc:
            raise ToolExecutionError("path escapes repository root") from exc
        return candidate

