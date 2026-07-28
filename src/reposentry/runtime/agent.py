from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from reposentry.domain.models import (
    AgentRunResult,
    ModelMessage,
    RunStatus,
)
from reposentry.runtime.budget import AgentBudget, BudgetExceeded
from reposentry.runtime.events import EventBus, RuntimeEvent
from reposentry.runtime.model import ModelClient
from reposentry.runtime.tools import ToolExecutionError, ToolRegistry


@dataclass(frozen=True)
class AgentSpec:
    name: str
    instructions: str
    allowed_tools: List[str] = field(default_factory=list)
    max_steps: int = 8
    max_tool_calls: int = 12
    max_tokens: int = 20_000
    timeout_seconds: float = 30.0


class AgentRuntime:
    """A small ReAct-style runtime with explicit budgets and tool contracts."""

    def __init__(
        self,
        model: ModelClient,
        tools: ToolRegistry,
        event_bus: Optional[EventBus] = None,
        repeated_call_limit: int = 2,
    ) -> None:
        self._model = model
        self._tools = tools
        self._events = event_bus or EventBus()
        self._repeated_call_limit = repeated_call_limit

    @property
    def events(self) -> EventBus:
        return self._events

    async def run(self, task_id: str, spec: AgentSpec, task: str) -> AgentRunResult:
        budget = AgentBudget(
            max_steps=spec.max_steps,
            max_tool_calls=spec.max_tool_calls,
            max_tokens=spec.max_tokens,
            timeout_seconds=spec.timeout_seconds,
        )
        try:
            return await asyncio.wait_for(
                self._run_loop(task_id, spec, task, budget),
                timeout=budget.timeout_seconds,
            )
        except asyncio.TimeoutError:
            await self._emit(task_id, spec.name, "agent.timeout", {})
            return self._result(
                spec,
                budget,
                RunStatus.BUDGET_EXCEEDED,
                error="agent execution timed out",
            )

    async def _run_loop(
        self,
        task_id: str,
        spec: AgentSpec,
        task: str,
        budget: AgentBudget,
    ) -> AgentRunResult:
        messages = [
            ModelMessage(role="system", content=spec.instructions),
            ModelMessage(role="user", content=task),
        ]
        call_counts: Dict[str, int] = {}
        await self._emit(task_id, spec.name, "agent.started", {"task": task})

        try:
            while True:
                budget.consume_step()
                await self._emit(
                    task_id,
                    spec.name,
                    "model.requested",
                    {"step": budget.steps},
                )
                response = await self._model.complete(
                    messages=messages,
                    tools=self._tools.schemas(spec.allowed_tools),
                )
                budget.consume_tokens(response.total_tokens)
                messages.append(
                    ModelMessage(
                        role="assistant",
                        content=response.content,
                        tool_calls=response.tool_calls,
                    )
                )

                if not response.tool_calls:
                    await self._emit(
                        task_id,
                        spec.name,
                        "agent.completed",
                        {"step": budget.steps},
                    )
                    return self._result(
                        spec,
                        budget,
                        RunStatus.COMPLETED,
                        output=response.content,
                    )

                for call in response.tool_calls:
                    budget.consume_tool_call()
                    signature = "{}:{}".format(
                        call.name,
                        json.dumps(call.arguments, sort_keys=True, ensure_ascii=False),
                    )
                    call_counts[signature] = call_counts.get(signature, 0) + 1

                    if call_counts[signature] > self._repeated_call_limit:
                        tool_result = {
                            "ok": False,
                            "error": "repeated identical tool call blocked",
                        }
                        await self._emit(
                            task_id,
                            spec.name,
                            "tool.blocked",
                            {"tool": call.name, "arguments": call.arguments},
                        )
                    else:
                        tool_result = await self._execute_tool(task_id, spec, call)

                    messages.append(
                        ModelMessage(
                            role="tool",
                            content=json.dumps(
                                tool_result,
                                ensure_ascii=False,
                                default=str,
                            ),
                            tool_call_id=call.call_id,
                        )
                    )
        except BudgetExceeded as exc:
            await self._emit(
                task_id,
                spec.name,
                "agent.budget_exceeded",
                {"error": str(exc)},
            )
            return self._result(
                spec,
                budget,
                RunStatus.BUDGET_EXCEEDED,
                error=str(exc),
            )
        except Exception as exc:
            await self._emit(
                task_id,
                spec.name,
                "agent.failed",
                {"error": str(exc)},
            )
            return self._result(
                spec,
                budget,
                RunStatus.FAILED,
                error=str(exc),
            )

    async def _execute_tool(self, task_id: str, spec: AgentSpec, call: object) -> object:
        await self._emit(
            task_id,
            spec.name,
            "tool.started",
            {"tool": call.name, "arguments": call.arguments},
        )
        try:
            value = await self._tools.execute(
                call.name,
                call.arguments,
                allowed_tools=spec.allowed_tools,
            )
            result = {"ok": True, "value": value}
            await self._emit(
                task_id,
                spec.name,
                "tool.completed",
                {"tool": call.name},
            )
            return result
        except ToolExecutionError as exc:
            await self._emit(
                task_id,
                spec.name,
                "tool.failed",
                {"tool": call.name, "error": str(exc)},
            )
            return {"ok": False, "error": str(exc)}

    @staticmethod
    def _result(
        spec: AgentSpec,
        budget: AgentBudget,
        status: RunStatus,
        output: str = "",
        error: Optional[str] = None,
    ) -> AgentRunResult:
        return AgentRunResult(
            agent=spec.name,
            status=status,
            output=output,
            steps=budget.steps,
            tool_calls=budget.tool_calls,
            total_tokens=budget.total_tokens,
            error=error,
        )

    async def _emit(
        self,
        task_id: str,
        agent: str,
        event_type: str,
        payload: Dict[str, object],
    ) -> None:
        await self._events.emit(
            RuntimeEvent(
                task_id=task_id,
                agent=agent,
                event_type=event_type,
                payload=payload,
            )
        )

