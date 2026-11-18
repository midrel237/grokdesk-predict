from __future__ import annotations

from .base import Desk


class Treasury(Desk):
    """Desk 6. VAULT banks profit, pays gas, tracks the P&L and sweeps the
    surplus to cold storage every few hours. If the wallet ever dips below
    the starting balance it freezes RISK, which stops all new entries until
    the wallet recovers or the head signs off.
    """

    name = "TREASURY"

    def __init__(self, bus, agents, ledger, risk, sweep_every_h: int = 6) -> None:
        super().__init__(bus, agents)
        self.ledger = ledger
        self.risk = risk
        self.sweep_every_h = sweep_every_h
        self.swept_total = 0.0

    def tick_hour(self, hour: int) -> float:
        # freeze on a real drawdown, lift once the wallet climbs back. a tiny
        # dip below the start does not stop the desk.
        if self.ledger.wallet_sol < self.ledger.start_sol * 0.6:
            self.risk.frozen = True
        elif self.ledger.wallet_sol >= self.ledger.start_sol * 0.9:
            self.risk.frozen = False
        if hour and hour % self.sweep_every_h == 0:
            moved = self.ledger.sweep(keep=self.ledger.start_sol * 1.5)
            self.swept_total += moved
            return moved
        return 0.0
