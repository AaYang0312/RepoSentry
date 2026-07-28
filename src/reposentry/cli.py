from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import List, Optional

from reposentry.domain.models import AnalysisRequest
from reposentry.services.analysis import AnalysisService
from reposentry.services.revisions import RevisionService
from reposentry.skills.git import GitError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a RepoSentry analysis with the configured model provider."
    )
    parser.add_argument("--repo", required=True, help="Local repository path")
    parser.add_argument("--pr", type=int, default=None, help="Optional PR number")
    # Phase 2: authoritative revision pair. When both are present the change
    # set, route score, and risk flags are derived from Git.
    parser.add_argument(
        "--base",
        default=None,
        help="Base revision (commit SHA, branch, tag, or HEAD~N) to diff from",
    )
    parser.add_argument(
        "--head",
        default=None,
        help="Head revision to diff against; use with --base for a real PR",
    )
    # Legacy manual flags. Still accepted for non-git workflows, but ignored
    # when --base/--head are supplied (a deprecation notice is printed).
    parser.add_argument(
        "--changed-file",
        action="append",
        default=[],
        help="(deprecated, use --base/--head) Changed file; repeat for multiple",
    )
    parser.add_argument("--additions", type=int, default=0)
    parser.add_argument("--deletions", type=int, default=0)
    parser.add_argument("--dependency-changed", action="store_true")
    parser.add_argument("--api-contract-changed", action="store_true")
    parser.add_argument(
        "--sensitive-path",
        action="append",
        default=[],
    )
    return parser


def _build_request(args: argparse.Namespace, repository_path: Path) -> AnalysisRequest:
    base = args.base
    head = args.head
    legacy_used = any(
        (
            args.changed_file,
            args.additions,
            args.deletions,
            args.dependency_changed,
            args.api_contract_changed,
            args.sensitive_path,
        )
    )

    if base and head:
        if legacy_used:
            print(
                "warning: --base/--head supplied; manual --changed-file/--additions/"
                "--deletions/--dependency-changed/--api-contract-changed/"
                "--sensitive-path flags are ignored.",
                file=sys.stderr,
            )
        change_set = RevisionService().parse(
            base_ref=base,
            head_ref=head,
            repository_path=str(repository_path),
        )
        return RevisionService().build_request(
            repository_path=str(repository_path),
            change_set=change_set,
            pr_number=args.pr,
        )

    if base or head:
        raise ValueError("--base and --head must be supplied together")

    return AnalysisRequest(
        repository_path=str(repository_path),
        pr_number=args.pr,
        changed_files=args.changed_file,
        additions=max(0, args.additions),
        deletions=max(0, args.deletions),
        dependency_changed=args.dependency_changed,
        api_contract_changed=args.api_contract_changed,
        sensitive_paths=args.sensitive_path,
    )


async def run(args: argparse.Namespace) -> int:
    repository_path = Path(args.repo).expanduser().resolve()
    try:
        request = _build_request(args, repository_path)
    except GitError as exc:
        print("error: could not build change set: {}".format(exc), file=sys.stderr)
        return 2
    except ValueError as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 2
    job = await AnalysisService().run_now(request)
    print(json.dumps(job.to_dict(), indent=2, ensure_ascii=False))
    return 0 if job.status == "completed" else 1


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
