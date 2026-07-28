from __future__ import annotations

import json
import re
from typing import Any, Dict, List
from uuid import uuid4

from reposentry.domain.models import (
    ModelMessage,
    ModelResponse,
    ToolCall,
)
from reposentry.runtime.model import ModelClient


class DemoModelClient(ModelClient):
    """Deterministic no-key provider used for smoke tests and UI development."""

    async def complete(
        self,
        messages: List[ModelMessage],
        tools: List[Dict[str, Any]],
    ) -> ModelResponse:
        system = messages[0].content if messages else ""
        user = messages[1].content if len(messages) > 1 else ""

        if "VerifierAgent" in system:
            finding_ids = re.findall(
                r'"finding_id"\s*:\s*"([^"]+)"',
                user,
            )
            return ModelResponse(
                content=json.dumps(
                    {
                        "accepted_finding_ids": finding_ids,
                        "rejected": [],
                        "summary": "Demo verifier accepted well-formed evidence.",
                    }
                ),
                total_tokens=24,
            )

        tool_messages = [item for item in messages if item.role == "tool"]
        available_names = [item["name"] for item in tools]
        if not tool_messages and "list_files" in available_names:
            return ModelResponse(
                tool_calls=[
                    ToolCall(
                        call_id=str(uuid4()),
                        name="list_files",
                        arguments={"path": ".", "max_files": 20},
                    )
                ],
                total_tokens=16,
            )

        evidence_path = "README.md"
        if tool_messages:
            try:
                payload = json.loads(tool_messages[-1].content)
                files = payload.get("value", {}).get("files", [])
                if files:
                    evidence_path = files[0]
            except (TypeError, ValueError, AttributeError):
                pass

        agent_name = self._agent_name(system)
        return ModelResponse(
            content=json.dumps(
                {
                    "summary": "{} completed a demo inspection.".format(agent_name),
                    "findings": [
                        {
                            "category": "demo_observation",
                            "summary": (
                                "Demo provider inspected repository context; "
                                "replace it with a real model for substantive findings."
                            ),
                            "severity": "info",
                            "confidence": 0.5,
                            "recommendation": "Configure REPOSENTRY_MODEL_PROVIDER=openai.",
                            "evidence": [
                                {
                                    "path": evidence_path,
                                    "line_start": 1,
                                }
                            ],
                        }
                    ],
                }
            ),
            total_tokens=48,
        )

    @staticmethod
    def _agent_name(system: str) -> str:
        match = re.search(r"You are ([A-Za-z]+Agent)", system)
        return match.group(1) if match else "ReviewAgent"

