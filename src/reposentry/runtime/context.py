from __future__ import annotations

import asyncio
from copy import deepcopy
from typing import Any, Dict, List


class ArtifactStore:
    """Shared task state.

    Agents exchange structured artifacts here instead of appending unbounded
    chat transcripts to a shared prompt.
    """

    def __init__(self) -> None:
        self._values: Dict[str, Any] = {}
        self._lock = asyncio.Lock()

    async def put(self, key: str, value: Any) -> None:
        async with self._lock:
            self._values[key] = deepcopy(value)

    async def append(self, key: str, value: Any) -> None:
        async with self._lock:
            current = self._values.setdefault(key, [])
            if not isinstance(current, list):
                raise TypeError("artifact {} is not a list".format(key))
            current.append(deepcopy(value))

    async def snapshot(self) -> Dict[str, Any]:
        async with self._lock:
            return deepcopy(self._values)

