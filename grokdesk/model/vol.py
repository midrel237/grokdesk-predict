"""Estimateurs de volatilité réalisée, par seconde.

L'unité est volontairement « par seconde » et non annualisée : les fenêtres
font 5 à 15 minutes, et annualiser pour désannualiser ensuite n'ajoute que des
occasions de se tromper de facteur.
"""
from __future__ import annotations

import math
from collections import deque


class EwmaVol:
    """Vol réalisée en EWMA sur les log-returns, en unités par seconde.

    EWMA plutôt que fenêtre glissante simple parce que la vol crypto change de
    régime en quelques minutes : sur un horizon de 5 min, un estimateur qui
    met 30 min à réagir price la mauvaise distribution.
    """

    def __init__(self, halflife_s: float = 120.0, floor: float = 1e-6) -> None:
        if halflife_s <= 0:
            raise ValueError("halflife_s doit être > 0")
        self.halflife_s = halflife_s
        self.floor = floor
        self._var_per_s: float | None = None
        self._last_price: float | None = None
        self._last_ts: float | None = None
        self.n = 0

    def update(self, price: float, ts: float) -> float:
        if price <= 0:
            raise ValueError("prix doit être > 0")
        if self._last_price is None or self._last_ts is None:
            self._last_price, self._last_ts = price, ts
            return self.value
        dt = ts - self._last_ts
        if dt <= 0:
            # tick hors séquence ou doublon : on garde le prix le plus récent
            self._last_price = price
            return self.value
        r = math.log(price / self._last_price)
        # variance par seconde portée par ce tick
        inst = (r * r) / dt
        lam = 0.5 ** (dt / self.halflife_s)
        self._var_per_s = inst if self._var_per_s is None else (
            lam * self._var_per_s + (1.0 - lam) * inst
        )
        self._last_price, self._last_ts = price, ts
        self.n += 1
        return self.value

    @property
    def value(self) -> float:
        """Sigma par seconde, planchée."""
        if self._var_per_s is None:
            return self.floor
        return max(self.floor, math.sqrt(self._var_per_s))

    def over(self, horizon_s: float) -> float:
        """Sigma sur l'horizon : sigma_s * sqrt(T)."""
        return self.value * math.sqrt(max(0.0, horizon_s))

    @property
    def ready(self) -> bool:
        """En dessous d'une trentaine de ticks, l'estimateur est trop bruité
        pour qu'on en tire une probabilité exploitable."""
        return self.n >= 30


def parkinson_per_s(highs, lows, bar_seconds: float) -> float:
    """Estimateur de Parkinson sur des barres high/low.

    Plus efficace que close-to-close à nombre d'observations égal, parce qu'il
    exploite l'amplitude intra-barre. Utile pour amorcer l'EWMA au démarrage,
    quand on n'a pas encore assez de ticks.
    """
    pairs = [(h, l) for h, l in zip(highs, lows) if h > 0 and l > 0 and h >= l]
    if not pairs or bar_seconds <= 0:
        return 0.0
    acc = sum(math.log(h / l) ** 2 for h, l in pairs)
    var_per_bar = acc / (4.0 * math.log(2.0) * len(pairs))
    return math.sqrt(max(0.0, var_per_bar) / bar_seconds)


class RealizedVol:
    """Somme des carrés de returns sur une fenêtre glissante de ticks.

    Gardé à côté de l'EWMA pour pouvoir comparer les deux en calibration :
    si les deux estimateurs divergent fortement, c'est un signe de régime
    instable et EDGE devrait s'abstenir plutôt que d'arbitrer.
    """

    def __init__(self, maxlen: int = 600, floor: float = 1e-6) -> None:
        self.floor = floor
        self._r2: deque[tuple[float, float]] = deque(maxlen=maxlen)
        self._last: tuple[float, float] | None = None

    def update(self, price: float, ts: float) -> float:
        if self._last is not None:
            lp, lts = self._last
            dt = ts - lts
            if dt > 0 and price > 0 and lp > 0:
                r = math.log(price / lp)
                self._r2.append((r * r, dt))
        self._last = (price, ts)
        return self.value

    @property
    def value(self) -> float:
        if not self._r2:
            return self.floor
        tot_r2 = sum(x for x, _ in self._r2)
        tot_dt = sum(d for _, d in self._r2)
        if tot_dt <= 0:
            return self.floor
        return max(self.floor, math.sqrt(tot_r2 / tot_dt))
