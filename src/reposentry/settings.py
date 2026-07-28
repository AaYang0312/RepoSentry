from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    model_provider: str = "demo"
    model_name: str = "gpt-5-mini"
    openai_api_key: str = ""
    max_parallel_agents: int = 3
    repository_root: str = ""

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            model_provider=os.getenv("REPOSENTRY_MODEL_PROVIDER", "demo").lower(),
            model_name=os.getenv("REPOSENTRY_MODEL_NAME", "gpt-5-mini"),
            openai_api_key=os.getenv("OPENAI_API_KEY", ""),
            max_parallel_agents=max(
                1,
                int(os.getenv("REPOSENTRY_MAX_PARALLEL_AGENTS", "3")),
            ),
            repository_root=os.getenv("REPOSENTRY_REPOSITORY_ROOT", ""),
        )

