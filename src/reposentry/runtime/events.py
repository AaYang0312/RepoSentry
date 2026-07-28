from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field
from typing import Any, Awaitable, Callable, Dict, List
from uuid import uuid4

from reposentry.domain.models import utc_now_iso


@dataclass(frozen=True)
class RuntimeEvent:
    task_id: str
    agent: str
    event_type: str
    payload: Dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: str(uuid4()))
    created_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


EventSubscriber = Callable[[RuntimeEvent], Awaitable[None]]


class EventBus:
    """In-memory event stream that can later be replaced by Redis or Kafka."""

    def __init__(self) -> None:
        self._events: List[RuntimeEvent] = []
        self._subscribers: List[EventSubscriber] = []
        self._lock = asyncio.Lock()

    def subscribe(self, subscriber: EventSubscriber) -> None:
        self._subscribers.append(subscriber)

    async def emit(self, event: RuntimeEvent) -> None:
        async with self._lock:
            self._events.append(event)
        if self._subscribers:
            await asyncio.gather(
                *(subscriber(event) for subscriber in self._subscribers),
                return_exceptions=True,
            )

    async def snapshot(self) -> List[RuntimeEvent]:
        async with self._lock:
            return list(self._events)

