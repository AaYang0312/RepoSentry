from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from uuid import uuid4

from reposentry.adapters.demo import DemoModelClient
from reposentry.adapters.openai_responses import OpenAIResponsesClient
from reposentry.domain.models import (
    AnalysisReport,
    AnalysisRequest,
    utc_now_iso,
)
from reposentry.orchestration.orchestrator import ReviewOrchestrator
from reposentry.runtime.agent import AgentRuntime, AgentSpec
from reposentry.runtime.events import EventBus
from reposentry.runtime.model import ModelClient
from reposentry.services.revisions import RevisionService, attach_change_set
from reposentry.settings import Settings
from reposentry.skills.git import GitError
from reposentry.skills.repository import RepositoryToolkit


@dataclass
class AnalysisJob:
    task_id: str
    request: AnalysisRequest
    status: str = "queued"
    report: Optional[AnalysisReport] = None
    error: Optional[str] = None
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)
    events: EventBus = field(default_factory=EventBus)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "request": self.request.to_dict(),
            "report": self.report.to_dict() if self.report else None,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class InMemoryJobStore:
    """Replace this boundary with PostgreSQL/Redis when persistence is needed."""

    def __init__(self) -> None:
        self._jobs: Dict[str, AnalysisJob] = {}
        self._lock = asyncio.Lock()

    async def put(self, job: AnalysisJob) -> None:
        async with self._lock:
            self._jobs[job.task_id] = job

    async def get(self, task_id: str) -> Optional[AnalysisJob]:
        async with self._lock:
            return self._jobs.get(task_id)


class AnalysisService:
    def __init__(
        self,
        settings: Optional[Settings] = None,
        store: Optional[InMemoryJobStore] = None,
    ) -> None:
        self._settings = settings or Settings.from_env()
        self._store = store or InMemoryJobStore()
        self._background_tasks: Set["asyncio.Task[None]"] = set()

    async def submit(self, request: AnalysisRequest) -> AnalysisJob:
        task_id = str(uuid4())
        job = AnalysisJob(task_id=task_id, request=request)
        await self._store.put(job)
        task = asyncio.create_task(self._run(job))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return job

    async def run_now(self, request: AnalysisRequest) -> AnalysisJob:
        job = AnalysisJob(task_id=str(uuid4()), request=request)
        await self._store.put(job)
        await self._run(job)
        return job

    async def get(self, task_id: str) -> Optional[AnalysisJob]:
        return await self._store.get(task_id)

    async def events(self, task_id: str) -> Optional[List[Dict[str, Any]]]:
        job = await self._store.get(task_id)
        if job is None:
            return None
        return [event.to_dict() for event in await job.events.snapshot()]

    async def _run(self, job: AnalysisJob) -> None:
        job.status = "running"
        job.updated_at = utc_now_iso()
        try:
            repository_root = self._resolve_repository(job.request.repository_path)
            request = self._maybe_attach_change_set(job.request, repository_root)
            if request is not job.request:
                job.request = request
            toolkit = RepositoryToolkit(repository_root)
            registry = toolkit.registry()

            def runtime_factory(spec: AgentSpec) -> AgentRuntime:
                return AgentRuntime(
                    model=self._model_client(),
                    tools=registry,
                    event_bus=job.events,
                )

            orchestrator = ReviewOrchestrator(
                runtime_factory=runtime_factory,
                repository_root=repository_root,
                max_parallel_agents=self._settings.max_parallel_agents,
            )
            job.report = await orchestrator.analyze(
                request=job.request,
                task_id=job.task_id,
            )
            job.status = "completed"
        except Exception as exc:
            job.status = "failed"
            job.error = str(exc)
        finally:
            job.updated_at = utc_now_iso()

    def _maybe_attach_change_set(
        self,
        request: AnalysisRequest,
        repository_root: Path,
    ) -> AnalysisRequest:
        """Resolve a revision pair into a change set so routing is server-derived.

        If the request already carries a serialized change set (e.g. the CLI
        parsed it), it is used as-is. Otherwise, when only the revision refs are
        present, we compute the change set here. Returns the request unchanged
        when no revision pair is supplied.
        """

        if not request.has_revision_pair:
            return request
        if request.change_set:
            return attach_change_set(request, request.change_set)
        change_set = RevisionService(self._settings).parse(
            base_ref=request.base_revision,
            head_ref=request.head_revision,
            repository_path=str(repository_root),
        )
        return attach_change_set(request, change_set.to_dict())

    def _resolve_repository(self, requested_path: str) -> Path:
        requested = Path(requested_path).expanduser().resolve()
        allowed_root_value = self._settings.repository_root
        if not allowed_root_value:
            return requested
        allowed_root = Path(allowed_root_value).expanduser().resolve()
        try:
            requested.relative_to(allowed_root)
        except ValueError as exc:
            raise ValueError(
                "repository path is outside REPOSENTRY_REPOSITORY_ROOT"
            ) from exc
        return requested

    def _model_client(self) -> ModelClient:
        if self._settings.model_provider == "demo":
            return DemoModelClient()
        if self._settings.model_provider == "openai":
            return OpenAIResponsesClient(
                model=self._settings.model_name,
                api_key=self._settings.openai_api_key,
            )
        raise ValueError(
            "unsupported model provider: {}".format(
                self._settings.model_provider
            )
        )

