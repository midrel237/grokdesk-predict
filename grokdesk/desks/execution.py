from __future__ import annotations

from .base import Desk


class Execution(Desk):
    """Desk 3. EXEC greenlights, SNIPER takes the early curve, ROUTER sizes
    the entry and owns the ladder out. AGENT 01..07 do the actual fills.

    None of these agents can see RADAR or RESEARCH. They only ever react to a
    candidate that already cleared the filter, and only if RISK allows it.
    """

    name = "EXECUTION"

    def __init__(self, bus, agents, broker, risk) -> None:
        super().__init__(bus, agents)
        self.broker = broker
        self.risk = risk

    async def on_candidate(self, payload) -> None:
        cand, coin, research_agent = payload
        if not self.risk.allow(coin):
            return
        traders = [a for a in self.alive_agents() if a.name.startswith("AGENT")]
        if not traders:
            return
        trader = traders[len(self.broker.open) % len(traders)]
        size = self.risk.size_for(trader)
        if size <= 0:
            return
        pos = self.broker.buy(coin, size, trader.name)
        coin.pos = pos
        coin.approved_by = research_agent
        trader.stats["entries"] = trader.stats.get("entries", 0) + 1
        self.risk.entries_this_hour += 1
        await self.bus.emit("entry", (coin, pos, trader))
