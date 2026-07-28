from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional


class ToolExecutionError(RuntimeError):
    pass


ToolHandler = Callable[..., Any]


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    parameters: Dict[str, Any]
    handler: ToolHandler

    def model_schema(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
            "strict": True,
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: Dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> None:
        if tool.name in self._tools:
            raise ValueError("tool already registered: {}".format(tool.name))
        self._tools[tool.name] = tool

    def get(self, name: str) -> Optional[ToolDefinition]:
        return self._tools.get(name)

    def schemas(self, allowed_tools: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        names = allowed_tools if allowed_tools is not None else list(self._tools)
        return [
            self._tools[name].model_schema()
            for name in names
            if name in self._tools
        ]

    async def execute(
        self,
        name: str,
        arguments: Dict[str, Any],
        allowed_tools: Optional[List[str]] = None,
    ) -> Any:
        if allowed_tools is not None and name not in allowed_tools:
            raise ToolExecutionError("tool is not allowed for this agent: {}".format(name))
        tool = self._tools.get(name)
        if tool is None:
            raise ToolExecutionError("unknown tool: {}".format(name))
        try:
            result = tool.handler(**arguments)
            if inspect.isawaitable(result):
                result = await result
            return result
        except TypeError as exc:
            raise ToolExecutionError(
                "invalid arguments for {}: {}".format(name, exc)
            ) from exc
        except ToolExecutionError:
            raise
        except Exception as exc:
            raise ToolExecutionError("{} failed: {}".format(name, exc)) from exc

