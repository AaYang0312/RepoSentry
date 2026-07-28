# RepoSentry contribution guide

## Scope

RepoSentry is an independent resume project. Do not import implementation code
from the parent `backend` package directly. Migrate behavior behind the
interfaces described in `docs/MIGRATION_GUIDE.md`.

## Architecture rules

- Keep `runtime/` provider-neutral and framework-free.
- Keep FastAPI and Pydantic imports inside `api/`.
- Add model providers through `ModelClient`.
- Add capabilities through `ToolRegistry`; do not hard-code tools in the loop.
- Repository tools are read-only by default and must remain inside their root.
- Agents exchange structured artifacts, not shared unbounded chat transcripts.
- Findings require repository-relative evidence before they reach the verifier.
- Any shell or test execution must be isolated behind a future sandbox skill.

## Verification

Run the dependency-free core suite:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  python3 -m unittest discover -s tests -v
```

Add a focused test whenever routing, budgets, tool permissions, evidence
validation, or provider parsing changes.

