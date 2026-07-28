import asyncio
import json
import unittest

from reposentry.domain.models import (
    ModelResponse,
    RunStatus,
    ToolCall,
)
from reposentry.runtime.agent import AgentRuntime, AgentSpec
from reposentry.runtime.events import EventBus
from reposentry.runtime.model import ModelClient
from reposentry.runtime.tools import ToolDefinition, ToolRegistry


class ScriptedModel(ModelClient):
    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            return ModelResponse(
                tool_calls=[
                    ToolCall(
                        call_id="call-1",
                        name="echo",
                        arguments={"value": "hello"},
                    )
                ],
                total_tokens=10,
            )
        return ModelResponse(
            content=json.dumps({"summary": "done", "findings": []}),
            total_tokens=5,
        )


class AgentRuntimeTests(unittest.TestCase):
    def test_runtime_executes_tool_and_emits_trace(self) -> None:
        async def scenario():
            registry = ToolRegistry()
            registry.register(
                ToolDefinition(
                    name="echo",
                    description="Echo a value",
                    parameters={
                        "type": "object",
                        "properties": {"value": {"type": "string"}},
                        "required": ["value"],
                        "additionalProperties": False,
                    },
                    handler=lambda value: {"echo": value},
                )
            )
            events = EventBus()
            runtime = AgentRuntime(
                model=ScriptedModel(),
                tools=registry,
                event_bus=events,
            )
            result = await runtime.run(
                "task-1",
                AgentSpec(
                    name="TestAgent",
                    instructions="Test",
                    allowed_tools=["echo"],
                ),
                "Run the test",
            )
            return result, await events.snapshot()

        result, events = asyncio.run(scenario())
        self.assertEqual(result.status, RunStatus.COMPLETED)
        self.assertEqual(result.steps, 2)
        self.assertEqual(result.tool_calls, 1)
        self.assertEqual(result.total_tokens, 15)
        event_types = [event.event_type for event in events]
        self.assertIn("tool.started", event_types)
        self.assertIn("tool.completed", event_types)
        self.assertEqual(event_types[-1], "agent.completed")


if __name__ == "__main__":
    unittest.main()

