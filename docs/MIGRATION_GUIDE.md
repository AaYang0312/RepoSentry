# Migration guide

Keep migrations behind the existing boundaries so the skeleton stays easy to
explain and test.

## Candidate code to migrate later

| Existing capability | Target boundary |
| --- | --- |
| LLM provider or retry logic | `adapters/` implementing `ModelClient` |
| Existing ReAct loop behavior | `runtime/agent.py` |
| Tool definitions | `skills/` through `ToolRegistry` |
| GitHub PR fetching | a new `services/github.py` |
| Repository state models | `domain/models.py` |
| Routing logic | `orchestration/router.py` |
| Harness constraints | `runtime/budget.py` and a future sandbox skill |
| SSE or WebSocket events | subscribe to `runtime/events.py` |
| Database persistence | replace `InMemoryJobStore` |

## Safe migration order

1. Add characterization tests around the existing method.
2. Implement the target interface without changing the orchestrator.
3. Run the old and new implementations on the same fixture.
4. Compare structured outputs rather than raw prose.
5. Switch the dependency injection point.
6. Delete the old implementation only after the comparison passes.

## Intentionally absent

- Remote repository cloning and GitHub App authentication.
- Tree-sitter symbol graph and hybrid retrieval.
- Docker test execution and command allowlists.
- Redis/PostgreSQL persistence.
- WebSocket/SSE streaming.
- Production tracing and evaluation datasets.

These are extension points, not hidden TODOs in the core loop.

