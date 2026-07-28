from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, status

from reposentry.api.schemas import AnalysisCreate, TaskAccepted
from reposentry.services.analysis import AnalysisService


def create_app(service: Optional[AnalysisService] = None) -> FastAPI:
    analysis_service = service or AnalysisService()
    app = FastAPI(
        title="RepoSentry",
        version="0.1.0",
        description="Evidence-grounded multi-agent PR review skeleton",
    )

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.post(
        "/api/v1/analyses",
        response_model=TaskAccepted,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def create_analysis(payload: AnalysisCreate) -> TaskAccepted:
        repository_path = Path(payload.repository_path).expanduser()
        if not repository_path.is_dir():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="repository_path must be an existing directory",
            )
        job = await analysis_service.submit(payload.to_domain())
        return TaskAccepted(task_id=job.task_id, status=job.status)

    @app.get("/api/v1/analyses/{task_id}")
    async def get_analysis(task_id: str) -> dict:
        job = await analysis_service.get(task_id)
        if job is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="analysis task not found",
            )
        return job.to_dict()

    @app.get("/api/v1/analyses/{task_id}/events")
    async def get_analysis_events(task_id: str) -> dict:
        events = await analysis_service.events(task_id)
        if events is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="analysis task not found",
            )
        return {"task_id": task_id, "events": events}

    return app


app = create_app()

