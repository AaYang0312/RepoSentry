from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field

from reposentry.domain.models import AnalysisRequest


class AnalysisCreate(BaseModel):
    repository_path: str = Field(min_length=1)
    pr_number: Optional[int] = Field(default=None, ge=1)
    changed_files: List[str] = Field(default_factory=list)
    additions: int = Field(default=0, ge=0)
    deletions: int = Field(default=0, ge=0)
    dependency_changed: bool = False
    api_contract_changed: bool = False
    sensitive_paths: List[str] = Field(default_factory=list)

    def to_domain(self) -> AnalysisRequest:
        return AnalysisRequest(
            repository_path=self.repository_path,
            pr_number=self.pr_number,
            changed_files=self.changed_files,
            additions=self.additions,
            deletions=self.deletions,
            dependency_changed=self.dependency_changed,
            api_contract_changed=self.api_contract_changed,
            sensitive_paths=self.sensitive_paths,
        )


class TaskAccepted(BaseModel):
    task_id: str
    status: str

