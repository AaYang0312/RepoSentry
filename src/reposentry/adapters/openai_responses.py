from __future__ import annotations

import json
from typing import Any, Dict, List

from reposentry.domain.models import (
    ModelMessage,
    ModelResponse,
    ToolCall,
)
from reposentry.runtime.model import ModelClient


class OpenAIResponsesClient(ModelClient):
    """OpenAI Responses API adapter.

    Provider-specific parsing is isolated here so the runtime remains portable.
    """

    def __init__(self, model: str, api_key: str = "") -> None:
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise RuntimeError(
                "OpenAI adapter requires the optional project dependencies"
            ) from exc
        self._client = AsyncOpenAI(api_key=api_key or None)
        self._model = model

    async def complete(
        self,
        messages: List[ModelMessage],
        tools: List[Dict[str, Any]],
    ) -> ModelResponse:
        response = await self._client.responses.create(
            model=self._model,
            input=self._to_input(messages),
            tools=tools,
        )
        tool_calls = []
        for item in response.output:
            if getattr(item, "type", "") != "function_call":
                continue
            raw_arguments = getattr(item, "arguments", "{}")
            try:
                arguments = json.loads(raw_arguments)
            except json.JSONDecodeError:
                arguments = {}
            tool_calls.append(
                ToolCall(
                    call_id=item.call_id,
                    name=item.name,
                    arguments=arguments,
                )
            )
        usage = getattr(response, "usage", None)
        total_tokens = int(getattr(usage, "total_tokens", 0) or 0)
        return ModelResponse(
            content=response.output_text or "",
            tool_calls=tool_calls,
            total_tokens=total_tokens,
        )

    @staticmethod
    def _to_input(messages: List[ModelMessage]) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        for message in messages:
            if message.role == "tool":
                items.append(
                    {
                        "type": "function_call_output",
                        "call_id": message.tool_call_id,
                        "output": message.content,
                    }
                )
                continue

            if message.content:
                items.append({"role": message.role, "content": message.content})
            for call in message.tool_calls:
                items.append(
                    {
                        "type": "function_call",
                        "call_id": call.call_id,
                        "name": call.name,
                        "arguments": json.dumps(
                            call.arguments,
                            ensure_ascii=False,
                        ),
                    }
                )
        return items

