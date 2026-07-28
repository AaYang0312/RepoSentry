"""Read-only Git skill that turns a revision pair into a ``ChangeSet``.

This module is the bridge between a local working tree and the rest of
RepoSentry. It performs only read-only Git operations, validates every ref
before handing it to a subprocess, and keeps every path it returns relative to
the repository root. There is no shell execution and no write to the tree.

The parsing logic lives in :class:`DiffParser` so it can be unit-tested from a
fixture diff with no Git binary on the path.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from reposentry.domain.changes import (
    ChangeSet,
    ChangedFile,
    DiffHunk,
    RepositoryRevision,
    is_api_contract_path,
    is_dependency_path,
    is_sensitive_path,
)


class GitError(RuntimeError):
    """Raised when a read-only Git operation fails or an argument is unsafe."""


# Allow SHAs, branches, tags, refs, and the common revision syntax
# (``HEAD~1``, ``HEAD^``, ``main:feature``), but reject anything that could be
# interpreted as a flag or shell metacharacter. Mirrors the spirit of the
# existing ``git_diff`` guard in ``repository.py``.
_REF_PATTERN = re.compile(r"^[A-Za-z0-9._/\-~^:]{1,200}$")
_HUNK_PATTERN = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_len>\d+))? \+(?P<new_start>\d+)(?:,(?P<new_len>\d+))? @@"
)
_DIFF_HEADER = re.compile(r"^diff --git a/(.+) b/(.+)$")

# Map Git's single-letter status to the canonical labels we expose.
_STATUS_MAP = {
    "A": "added",
    "M": "modified",
    "D": "deleted",
    "R": "renamed",
    "C": "copied",
    "T": "typechange",
}


def validate_ref(ref: str) -> str:
    """Return ``ref`` unchanged when it is safe to pass to Git, else raise."""

    if not isinstance(ref, str) or not ref:
        raise GitError("git ref must be a non-empty string")
    if ref.startswith("-"):
        raise GitError("git ref cannot begin with '-'")
    if not _REF_PATTERN.match(ref):
        raise GitError("git ref contains unsupported characters: {!r}".format(ref))
    return ref


class DiffParser:
    """Parse ``git diff`` output into ``ChangeSet`` value objects.

    The parser is intentionally stateless and pure: feed it the three pieces of
    Git output and it returns a fully populated :class:`ChangeSet`. No
    filesystem access, no subprocesses.
    """

    def parse(
        self,
        base: RepositoryRevision,
        head: RepositoryRevision,
        numstat: str,
        name_status: str,
        unified: str,
    ) -> ChangeSet:
        counts = self._parse_numstat(numstat)
        statuses = self._parse_name_status(name_status)
        hunks_by_path = self._parse_unified(unified)

        files: List[ChangedFile] = []
        all_paths = sorted(set(counts) | set(statuses) | set(hunks_by_path))
        for path in all_paths:
            if not ChangeSet.is_within_root(path):
                raise GitError("diff path escapes repository root: {}".format(path))
            additions, deletions = counts.get(path, (0, 0))
            files.append(
                ChangedFile(
                    path=path,
                    status=statuses.get(path, "modified"),
                    additions=additions,
                    deletions=deletions,
                    hunks=hunks_by_path.get(path, []),
                )
            )

        additions = sum(item.additions for item in files)
        deletions = sum(item.deletions for item in files)
        changed_paths = [item.path for item in files]
        sensitive = [p for p in changed_paths if is_sensitive_path(p)]

        return ChangeSet(
            base=base,
            head=head,
            files=files,
            additions=additions,
            deletions=deletions,
            dependency_changed=any(is_dependency_path(p) for p in changed_paths),
            api_contract_changed=any(is_api_contract_path(p) for p in changed_paths),
            sensitive_paths=sensitive,
        )

    # -- numstat ------------------------------------------------------------

    @staticmethod
    def _parse_numstat(numstat: str) -> Dict[str, Tuple[int, int]]:
        """Parse ``--numstat`` output into ``{path: (additions, deletions)}``.

        Binary files show ``-`` for both counts; renames may show
        ``add\tdel\told => new``. We collapse to the destination path.
        """

        counts: Dict[str, Tuple[int, int]] = {}
        for raw_line in numstat.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            add_raw, del_raw, path_raw = parts[0], parts[1], parts[2]
            additions = 0 if add_raw == "-" else _to_int(add_raw)
            deletions = 0 if del_raw == "-" else _to_int(del_raw)
            path = _destination_path(path_raw)
            counts[path] = (additions, deletions)
        return counts

    # -- name-status --------------------------------------------------------

    @staticmethod
    def _parse_name_status(name_status: str) -> Dict[str, str]:
        """Parse ``--name-status`` output into ``{path: status}``.

        Rename/copy lines are ``Rxxx\\told\\tnew``; we record the destination.
        """

        statuses: Dict[str, str] = {}
        for raw_line in name_status.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            parts = line.split("\t")
            code = parts[0]
            label = _STATUS_MAP.get(code[0].upper(), "modified")
            if len(parts) >= 3:
                # rename/copy: status<TAB>old<TAB>new -> use new path
                path = _destination_path(parts[-1])
            elif len(parts) == 2:
                path = _destination_path(parts[1])
            else:
                continue
            statuses[path] = label
        return statuses

    # -- unified ------------------------------------------------------------

    @staticmethod
    def _parse_unified(unified: str) -> Dict[str, List[DiffHunk]]:
        """Split a unified diff into ``{path: [DiffHunk, ...]}``.

        We key hunks by the ``b/`` side of each ``diff --git`` header so the
        result aligns with the numstat/name-status paths.
        """

        hunks: Dict[str, List[DiffHunk]] = {}
        current_path: Optional[str] = None
        current_hunk: Optional[Dict[str, object]] = None
        body: List[str] = []

        def flush_hunk() -> None:
            nonlocal current_hunk, body
            if current_hunk is not None and current_path is not None:
                current_hunk["body"] = "\n".join(body).rstrip("\n")
                hunks.setdefault(current_path, []).append(_hunk_from(current_hunk))
            current_hunk = None
            body = []

        for raw_line in unified.splitlines():
            header = _DIFF_HEADER.match(raw_line)
            if header:
                flush_hunk()
                # group(2) is the b/ path; strip surrounding quotes if present.
                current_path = _strip_quotes(header.group(2))
                continue
            hunk_match = _HUNK_PATTERN.match(raw_line)
            if hunk_match:
                flush_hunk()
                current_hunk = {
                    "old_start": int(hunk_match.group("old_start") or 0),
                    "old_len": int(hunk_match.group("old_len") or 1),
                    "new_start": int(hunk_match.group("new_start") or 0),
                    "new_len": int(hunk_match.group("new_len") or 1),
                }
                continue
            if current_hunk is not None:
                body.append(raw_line)
        flush_hunk()
        return hunks


def _to_int(value: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _destination_path(path_raw: str) -> str:
    """Collapse ``old => new`` rename markers to the destination path."""

    if "=>" in path_raw:
        # Forms: "old => new" or "prefix/{old => new}/suffix"
        right = path_raw.rsplit("=>", 1)[-1].strip()
        return _strip_quotes(right)
    return _strip_quotes(path_raw)


def _strip_quotes(path: str) -> str:
    path = path.strip()
    if len(path) >= 2 and path.startswith('"') and path.endswith('"'):
        return path[1:-1]
    return path


def _hunk_from(values: Dict[str, object]) -> DiffHunk:
    return DiffHunk(
        old_start=int(values["old_start"]),  # type: ignore[arg-type]
        old_len=int(values["old_len"]),  # type: ignore[arg-type]
        new_start=int(values["new_start"]),  # type: ignore[arg-type]
        new_len=int(values["new_len"]),  # type: ignore[arg-type]
        body=str(values.get("body", "")),
    )


class GitClient:
    """Read-only Git wrapper constrained to a single repository root."""

    def __init__(self, repository_root: Path) -> None:
        root = Path(repository_root).expanduser().resolve()
        if not root.is_dir():
            raise GitError("repository path does not exist: {}".format(root))
        self._root = root
        self._parser = DiffParser()

    @property
    def root(self) -> Path:
        return self._root

    # -- revisions ----------------------------------------------------------

    def resolve_revision(self, ref: str) -> str:
        """Resolve ``ref`` to a 40-char commit SHA via ``git rev-parse``."""

        validate_ref(ref)
        completed = self._run(
            ["rev-parse", "--verify", "{}^{{commit}}".format(ref)]
        )
        sha = completed.stdout.strip()
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise GitError("could not resolve ref to a commit SHA: {}".format(ref))
        return sha

    def revision(self, ref: str) -> RepositoryRevision:
        return RepositoryRevision(repository_path=str(self._root), sha=self.resolve_revision(ref))

    # -- diffs --------------------------------------------------------------

    def diff(self, base_ref: str, head_ref: str) -> ChangeSet:
        """Build a :class:`ChangeSet` between ``base_ref`` and ``head_ref``."""

        base = self.revision(base_ref)
        head = self.revision(head_ref)
        numstat = self._run(
            ["diff", "--no-ext-diff", "--numstat", base.sha, head.sha, "--"]
        ).stdout
        name_status = self._run(
            ["diff", "--no-ext-diff", "--name-status", base.sha, head.sha, "--"]
        ).stdout
        unified = self._run(
            [
                "diff",
                "--no-ext-diff",
                "--unified=3",
                base.sha,
                head.sha,
                "--",
            ]
        ).stdout
        return self._parser.parse(base, head, numstat, name_status, unified)

    # -- subprocess plumbing ------------------------------------------------

    def _run(self, args: List[str], timeout: int = 15) -> subprocess.CompletedProcess:
        command = ["git", "-C", str(self._root), *args]
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GitError("git command failed: {}".format(exc)) from exc
        if completed.returncode != 0:
            message = completed.stderr.strip() or "git command failed"
            raise GitError(message)
        return completed
