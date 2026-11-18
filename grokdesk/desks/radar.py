from __future__ import annotations

from .base import Desk
from ..types import Signal


class Radar(Desk):
    """Desk 1. SCOUT, FEED and SIGNAL watch trending posts, fresh mints and
    whale wallets. They read the whole board and buy nothing. The only thing
    they ship is a Signal to the next desk.

    Each agent has its own polling interval. A slow poller misses coins that
    already graduated, which is exactly the kind of drift AUDIT catches.
    """

    name = "RADAR"

    def __init__(self, bus, agents, poll_s: dict[str, int]) -> None:
        super().__init__(bus, agents)
        self.poll_s = poll_s

    async def observe(self, coin, tick: int) -> None:
        agent = self._pick(tick)
        if agent is None:
            return
        agent.stats["seen"] = agent.stats.get("seen", 0) + 1
        # a signal on something that already graduated is stale and worthless
        if coin.graduated and self.poll_s.get(agent.name, 4) > 3:
            agent.stats["stale"] = agent.stats.get("stale", 0) + 1
            agent.credit(False, 0.09)
            return
        sig = Signal(coin.mint, coin.symbol, agent.name, coin.trend,
                     whale_flow=coin.curve.real_sol, age_s=coin.age_s,
                     graduated=coin.graduated)
        agent.credit(True, 0.004)
        await self.bus.emit("signal", (sig, coin))

    def _pick(self, tick: int):
        agents = self.alive_agents()
        return agents[tick % len(agents)] if agents else None
