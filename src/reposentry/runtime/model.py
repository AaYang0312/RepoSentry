from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List

from reposentry.domain.models import ModelMessage, ModelResponse


class ModelClient(ABC):
    """Provider-neutral model boundary used by the agent loop."""

    @abstractmethod
    async def complete(
        self,
        messages: List[ModelMessage],
        tools: List[Dict[str, Any]],
    ) -> ModelResponse:
        raise NotImplementedError

