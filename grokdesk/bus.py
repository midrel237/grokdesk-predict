from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any, Awaitable, Callable

Handler = Callable[[Any], Awaitable[None]]


class Bus:
    """A tiny async pub/sub. Desks talk to each other through topics only,
    which is what keeps EXECUTION from ever seeing RADAR directly: it just
    subscribes to 'candidate' and reacts to whatever cleared the filter."""

    def __init__(self) -> None:
        self._subs: dict[str, list[Handler]] = defaultdict(list)

    def on(self, topic: str, handler: Handler) -> None:
        self._subs[topic].append(handler)

    async def emit(self, topic: str, payload: Any) -> None:
        handlers = list(self._subs.get(topic, ()))
        if not handlers:
            return
        await asyncio.gather(*(h(payload) for h in handlers))
