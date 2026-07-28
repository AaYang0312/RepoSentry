from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import List, Optional

from reposentry.domain.models import AnalysisRequest
from reposentry.services.analysis import AnalysisService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a RepoSentry analysis with the configured model provider."
    )
    parser.add_argument("--repo", required=True, help="Local repository path")
    parser.add_argument("--pr", type=int, default=None, help="Optional PR number")
    parser.add_argument(
        "--changed-file",
        action="append",
        default=[],
        help="Changed file; repeat the flag for multiple files",
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


async def run(args: argparse.Namespace) -> int:
    repository_path = Path(args.repo).expanduser().resolve()
    request = AnalysisRequest(
        repository_path=str(repository_path),
        pr_number=args.pr,
        changed_files=args.changed_file,
        additions=max(0, args.additions),
        deletions=max(0, args.deletions),
        dependency_changed=args.dependency_changed,
        api_contract_changed=args.api_contract_changed,
        sensitive_paths=args.sensitive_path,
    )
    job = await AnalysisService().run_now(request)
    print(json.dumps(job.to_dict(), indent=2, ensure_ascii=False))
    return 0 if job.status == "completed" else 1


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())

