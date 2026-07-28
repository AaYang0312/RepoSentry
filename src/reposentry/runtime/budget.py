from __future__ import annotations

from dataclasses import dataclass


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class AgentBudget:
    max_steps: int = 8
    max_tool_calls: int = 12
    max_tokens: int = 20_000
    timeout_seconds: float = 30.0
    steps: int = 0
    tool_calls: int = 0
    total_tokens: int = 0

    def consume_step(self) -> None:
        if self.steps >= self.max_steps:
            raise BudgetExceeded("maximum reasoning steps exceeded")
        self.steps += 1

    def consume_tool_call(self) -> None:
        if self.tool_calls >= self.max_tool_calls:
            raise BudgetExceeded("maximum tool calls exceeded")
        self.tool_calls += 1

    def consume_tokens(self, count: int) -> None:
        self.total_tokens += max(0, count)
        if self.total_tokens > self.max_tokens:
            raise BudgetExceeded("maximum token budget exceeded")

