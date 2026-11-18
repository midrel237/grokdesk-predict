"""CLOSER — surveille la résolution, calcule le P&L réel, synchronise le ledger.

Entièrement nouveau : la version pump.fun n'avait aucun équivalent, elle
clôturait par expiration d'un compteur de ticks avec un multiple décidé à
l'avance.
"""
from __future__ import annotations

from datetime import date

from ..core.bus import Bus
from ..core.ledger import Ledger
from ..core.types import Closed, Order, Resolution
from ..execution.paper import PaperEngine
from ..model.calibration import Calibrator
from ..risk.engine import RiskEngine


class Closer:
    def __init__(self, bus: Bus, engine: PaperEngine, ledger: Ledger,
                 risk: RiskEngine, calibrator: Calibrator) -> None:
        self.bus = bus
        self.engine = engine
        self.ledger = ledger
        self.risk = risk
        self.cal = calibrator
        self.closed: list[Closed] = []

    async def resolve(self, order: Order, resolution: Resolution,
                      payout: float, today: date) -> Closed:
        c = self.engine.resolve(order, resolution, payout)
        self.ledger.settle(c, today)
        self.risk.open_positions = max(0, self.risk.open_positions - 1)
        self.cal.record_bet(c.predicted_p, c.won, payout)
        self.closed.append(c)
        await self.bus.emit("closed", c)
        return c

    async def observe_unplayed(self, p_up: float, resolution: Resolution) -> None:
        """Enregistre aussi les fenêtres où l'on n'a PAS parié.

        Essentiel : parier uniquement quand on est confiant biaise la mesure
        du modèle. La calibration de PRIOR doit être évaluée sur toutes les
        fenêtres observées, pas sur le sous-ensemble sélectionné par EDGE.
        """
        from ..core.types import Side
        self.cal.record_forecast(p_up, resolution.outcome is Side.UP)
