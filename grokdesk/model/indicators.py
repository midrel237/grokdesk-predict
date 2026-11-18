"""Indicateurs : Elder Ray (demandé par le plan) et microstructure.

Position assumée sur Elder Ray : il est implémenté parce que le plan le
demande et qu'il est bon marché à mesurer, mais il est construit sur une EMA
13 — son retard est du même ordre que l'horizon de trade sur une fenêtre de
5 minutes. Il sert donc de *filtre mesurable*, pas de source d'edge.

L'edge exploitable, quand il existe, est dans la microstructure : déséquilibre
de carnet et momentum de microprix. D'où `BookImbalance` ci-dessous.
"""
from __future__ import annotations

from collections import deque


class Ema:
    """EMA incrémentale. alpha = 2/(n+1), convention Elder."""

    def __init__(self, period: int = 13) -> None:
        if period < 1:
            raise ValueError("period doit être >= 1")
        self.period = period
        self.alpha = 2.0 / (period + 1.0)
        self.value: float | None = None
        self.n = 0

    def update(self, x: float) -> float:
        self.n += 1
        self.value = x if self.value is None else (
            self.alpha * x + (1.0 - self.alpha) * self.value
        )
        return self.value

    @property
    def ready(self) -> bool:
        return self.n >= self.period


class ElderRay:
    """Bull Power = high - EMA13, Bear Power = low - EMA13, Net = somme.

    Alimenté par barres. Sur des ticks, agréger d'abord en barres de 5-15 s.
    """

    def __init__(self, period: int = 13) -> None:
        self.ema = Ema(period)
        self.bull = 0.0
        self.bear = 0.0

    def update(self, high: float, low: float, close: float) -> dict[str, float]:
        base = self.ema.update(close)
        self.bull = high - base
        self.bear = low - base
        return self.snapshot

    @property
    def net(self) -> float:
        return self.bull + self.bear

    @property
    def snapshot(self) -> dict[str, float]:
        return {
            "ema13": self.ema.value if self.ema.value is not None else 0.0,
            "bull_power": self.bull,
            "bear_power": self.bear,
            "net_power": self.net,
            "elder_ready": 1.0 if self.ema.ready else 0.0,
        }


class BookImbalance:
    """Déséquilibre de carnet pondéré par la profondeur, dans [-1, 1].

    > 0 : pression acheteuse. C'est le candidat sérieux à l'edge sur les
    marchés à carnet (Polymarket). Inapplicable chez un broker binaire qui
    est lui-même la contrepartie : il n'y a pas de carnet à lire, ce qui est
    exactement l'argument contre l'exécution sur ce type de venue.
    """

    def __init__(self, levels: int = 5) -> None:
        self.levels = levels
        self.value = 0.0

    def update(self, bids: list[tuple[float, float]],
               asks: list[tuple[float, float]]) -> float:
        bq = sum(q for _, q in bids[: self.levels])
        aq = sum(q for _, q in asks[: self.levels])
        tot = bq + aq
        self.value = 0.0 if tot <= 0 else (bq - aq) / tot
        return self.value


class MicropriceMomentum:
    """Variation du microprix sur une fenêtre glissante de N ticks."""

    def __init__(self, lookback: int = 20) -> None:
        self._buf: deque[float] = deque(maxlen=lookback)
        self.value = 0.0

    def update(self, microprice: float) -> float:
        self._buf.append(microprice)
        if len(self._buf) >= 2 and self._buf[0] > 0:
            self.value = (self._buf[-1] - self._buf[0]) / self._buf[0]
        return self.value
