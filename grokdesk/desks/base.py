from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Agent:
    """One worker on a desk. `score` is a rolling read on how good its recent
    calls were; the head of desk fires whoever sits at the bottom. `prompt_rev`
    counts how many times AUDIT has rewritten this agent."""

    name: str
    desk: str
    budget: float = 1.0
    score: float = 0.5
    alive: bool = True
    prompt_rev: int = 0
    stats: dict = field(default_factory=dict)

    def credit(self, good: bool, w: float = 0.1) -> None:
        self.score = max(0.0, min(1.0, self.score + (w if good else -w)))


class Desk:
    name = "DESK"

    def __init__(self, bus, agents: list[Agent]) -> None:
        self.bus = bus
        self.agents = {a.name: a for a in agents}

    def alive_agents(self) -> list[Agent]:
        return [a for a in self.agents.values() if a.alive]
