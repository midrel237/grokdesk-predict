from __future__ import annotations

from .base import Desk


class Risk(Desk):
    """Desk 4. One agent, and it outranks everyone including the head.

    It caps any single position at `max_frac` of the wallet, blocks the
    fourth open position until one closes, and can be frozen by TREASURY.
    When it says no, no one overrides it.
    """

    name = "RISK"

    def __init__(self, bus, agents, broker, ledger, max_frac: float = 0.15,
                 max_open: int = 3, max_per_hour: int = 6) -> None:
        super().__init__(bus, agents)
        self.broker = broker
        self.ledger = ledger
        self.max_frac = max_frac
        self.max_open = max_open
        self.max_per_hour = max_per_hour
        self.entries_this_hour = 0
        self.frozen = False
        self.blocks = 0

    def allow(self, coin) -> bool:
        if (self.frozen
                or len(self.broker.open) >= self.max_open
                or self.entries_this_hour >= self.max_per_hour):
            self.blocks += 1
            return False
        return True

    def size_for(self, agent) -> float:
        cap = self.ledger.wallet_sol * self.max_frac
        want = 0.25 * max(0.2, agent.budget)
        return round(max(0.0, min(cap, want)), 4)
