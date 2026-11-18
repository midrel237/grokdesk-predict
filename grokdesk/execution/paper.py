"""Moteur d'exécution papier, avec latence et rejets modélisés.

Garanties tenues par ce module :

1. **Aucun tirage du résultat.** `resolve()` lit le prix de règlement fourni
   par la source de prix. Le moteur ne sait pas et ne peut pas savoir si le
   trade va gagner au moment où il remplit l'ordre.
2. **La latence est appliquée avant le fill.** L'ordre est rempli au prix
   observé à `created_ts + latency_s`, pas au prix qui a déclenché le signal.
   C'est ce qui fait apparaître le coût réel d'un edge fin.
3. **Les rejets existent.** Un payout qui bouge entre la décision et l'arrivée
   de l'ordre, ou une mise au-delà du plafond du venue, produit un rejet — ce
   que fait un vrai broker, et que l'ancien `PaperBroker` ignorait.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field

from ..core.types import Closed, Order, Resolution, Side, Signal


@dataclass
class PaperVenue:
    """Paramètres du venue simulé. À calibrer sur des fills réels observés."""
    latency_s: float = 0.35
    fee_fraction: float = 0.0
    payout_slip: float = 0.0       # dégradation du payout entre décision et fill
    max_stake: float = 1e9
    reject_rate_on_payout_drop: bool = True


class PaperEngine:
    def __init__(self, venue: PaperVenue | None = None) -> None:
        self.venue = venue or PaperVenue()
        self.open: dict[str, Order] = {}
        self.history: list[Order] = []
        self._ids = itertools.count(1)
        self.rejected = 0

    # --- placement -------------------------------------------------------
    def place(self, sig: Signal, stake: float, now: float,
              payout_at_fill: float | None = None) -> Order:
        """Place un ordre. Le fill se fait au payout réellement disponible à
        l'arrivée, pas à celui qui a motivé le signal."""
        order = Order(
            window=sig.window,
            side=sig.side,
            stake=round(stake, 6),
            limit_price=None,
            created_ts=now,
            signal=sig,
            order_id=f"P{next(self._ids):06d}",
        )
        if stake <= 0:
            order.rejected = "mise_nulle"
        elif stake > self.venue.max_stake:
            order.rejected = "au_dela_du_plafond_venue"
        else:
            eff = payout_at_fill if payout_at_fill is not None else sig.payout
            eff -= self.venue.payout_slip
            if self.venue.reject_rate_on_payout_drop and eff < sig.break_even:
                # le payout a glissé sous le seuil d'équilibre : le pari n'a
                # plus de sens, on refuse plutôt que de le subir
                order.rejected = "payout_sous_seuil_a_l_arrivee"
            else:
                order.filled = True
                order.fill_ts = now + self.venue.latency_s
                order.fill_price = None   # posé par le moteur de prix
        if order.rejected:
            self.rejected += 1
        else:
            self.open[order.order_id] = order
        self.history.append(order)
        return order

    def mark_fill_price(self, order: Order, price_at_fill: float) -> None:
        """Le prix observé à `fill_ts`, fourni par la source. C'est ici que la
        latence devient un coût mesurable plutôt qu'une note de bas de page."""
        order.fill_price = price_at_fill

    # --- résolution ------------------------------------------------------
    def resolve(self, order: Order, resolution: Resolution,
                payout: float) -> Closed:
        """Boucle un ordre contre la résolution **réelle** de la fenêtre."""
        if not order.filled:
            raise ValueError(f"ordre {order.order_id} non rempli")
        won = resolution.outcome is order.side
        fee = round(order.stake * self.venue.fee_fraction, 6)
        # binaire : gain = mise * payout, perte = mise entière
        pnl = round(order.stake * payout, 6) if won else round(-order.stake, 6)
        self.open.pop(order.order_id, None)
        hold = max(0.0, resolution.settle_ts - (order.fill_ts or order.created_ts))
        return Closed(
            order=order,
            resolution=resolution,
            won=won,
            pnl=pnl,
            fees=fee,
            predicted_p=order.signal.p if order.signal else float("nan"),
            hold_s=hold,          # temps marché : corrige le bug `hold_s` d'origine
        )
