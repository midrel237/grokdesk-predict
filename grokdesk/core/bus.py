"""Bus pub/sub asynchrone — version durcie de l'original `grokdesk/bus.py`.

Deux corrections par rapport à la version pump.fun :

1. `asyncio.gather(..., return_exceptions=True)` : dans l'original, une
   exception dans un handler annulait tous les autres handlers du même topic.
   Sur un pipeline qui touche de l'argent, un bug dans l'AUDIT ne doit pas
   empêcher le CLOSER de boucler une position.
2. Les payloads sont des dataclasses de `core.types`, plus des tuples
   anonymes, et `emit` vérifie le type attendu quand il est déclaré.
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any, Awaitable, Callable

log = logging.getLogger(__name__)

Handler = Callable[[Any], Awaitable[None]]


class Bus:
    def __init__(self) -> None:
        self._subs: dict[str, list[Handler]] = defaultdict(list)
        self._contract: dict[str, type] = {}
        self.errors: list[tuple[str, str, BaseException]] = []
        self.counts: dict[str, int] = defaultdict(int)

    def declare(self, topic: str, payload_type: type) -> None:
        """Fige le type attendu sur un topic. `emit` refusera autre chose."""
        self._contract[topic] = payload_type

    def on(self, topic: str, handler: Handler) -> None:
        self._subs[topic].append(handler)

    async def emit(self, topic: str, payload: Any) -> None:
        expected = self._contract.get(topic)
        if expected is not None and not isinstance(payload, expected):
            raise TypeError(
                f"topic '{topic}' attend {expected.__name__}, "
                f"reçu {type(payload).__name__}"
            )
        self.counts[topic] += 1
        handlers = list(self._subs.get(topic, ()))
        if not handlers:
            return
        results = await asyncio.gather(
            *(h(payload) for h in handlers), return_exceptions=True
        )
        for h, res in zip(handlers, results):
            if isinstance(res, BaseException):
                name = getattr(h, "__qualname__", repr(h))
                self.errors.append((topic, name, res))
                log.exception("handler %s a échoué sur '%s'", name, topic,
                              exc_info=res)
