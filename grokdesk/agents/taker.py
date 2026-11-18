"""TAKER — transforme un signal autorisé en ordre.

Remplace EXEC/SNIPER/ROUTER + AGENT 01..07. Les sept « traders » de la version
pump.fun étaient un artefact de démo, et quatre d'entre eux ne tradaient même
jamais : `execution.py` choisissait le trader par
`traders[len(broker.open) % len(traders)]` alors que RISK bloquait au-delà de
3 positions ouvertes, donc l'index ne valait jamais que 0, 1 ou 2. Un seul
TAKER avec une vraie machine à états vaut mieux que sept décoratifs.
"""
from __future__ import annotations

from datetime import date

from ..core.bus import Bus
from ..core.ledger import Ledger
from ..core.types import Order, Signal
from ..execution.paper import PaperEngine
from ..risk.engine import RiskEngine


class Taker:
    def __init__(self, bus: Bus, engine: PaperEngine, risk: RiskEngine,
                 ledger: Ledger) -> None:
        self.bus = bus
        self.engine = engine
        self.risk = risk
        self.ledger = ledger
        self.placed = 0
        self.refused = 0

    async def on_signal(self, sig: Signal, now: float, today: date,
                        payout_at_fill: float | None = None) -> Order | None:
        """RISK d'abord, toujours. Aucun chemin ne contourne `allow()`."""
        block = self.risk.allow(sig, today)
        if block is not None:
            self.refused += 1
            await self.bus.emit("refused", (sig, block))
            return None
        stake = self.risk.stake_for(sig)
        order = self.engine.place(sig, stake, now, payout_at_fill)
        if order.rejected:
            self.refused += 1
            await self.bus.emit("refused", (sig, order.rejected))
            return None
        fee = round(stake * self.engine.venue.fee_fraction, 6)
        self.ledger.commit(stake, fee, today)
        self.risk.open_positions += 1
        self.risk.entries_this_hour += 1
        self.placed += 1
        await self.bus.emit("order", order)
        return order
