"""Interface commune des sources de prix, et chemin de prix pré-calculé.

`PricePath` est la pièce qui garantit l'honnêteté du moteur papier : le chemin
complet est produit **avant** que le pipeline ne tourne, et il ne dépend
d'aucune décision des agents. Le moteur ne peut consulter que le passé via
`at(ts)`, et la résolution lit le prix à l'expiration.

C'est l'inverse exact de l'ancien `runtime.on_entry()`, qui décidait du
résultat à l'entrée en fonction d'une qualité cachée du coin.
"""
from __future__ import annotations

import bisect
import math
import random
from typing import Iterator, Protocol

from ..core.types import Resolution, Tick, Window


class PriceSource(Protocol):
    """Toute source de prix exploitable par le moteur."""

    venue: str
    asset: str

    def ticks(self) -> Iterator[Tick]: ...
    def price_at(self, ts: float) -> float: ...


class PricePath:
    """Chemin de prix immuable, interrogeable par horodatage.

    `at()` fait une recherche dichotomique et renvoie le dernier prix connu à
    cet instant — jamais une interpolation vers le futur, ce qui serait une
    fuite d'information.
    """

    def __init__(self, asset: str, times: list[float], prices: list[float],
                 venue: str = "paper") -> None:
        if len(times) != len(prices) or not times:
            raise ValueError("times et prices doivent être non vides et de même taille")
        self.asset = asset
        self.venue = venue
        self._t = list(times)
        self._p = list(prices)

    def __len__(self) -> int:
        return len(self._t)

    @property
    def t0(self) -> float:
        return self._t[0]

    @property
    def t1(self) -> float:
        return self._t[-1]

    def at(self, ts: float) -> float:
        """Dernier prix observé à `ts` (constant par morceaux)."""
        if ts <= self._t[0]:
            return self._p[0]
        if ts >= self._t[-1]:
            return self._p[-1]
        i = bisect.bisect_right(self._t, ts) - 1
        return self._p[max(0, i)]

    def ticks(self) -> Iterator[Tick]:
        for t, p in zip(self._t, self._p):
            yield Tick(venue=self.venue, asset=self.asset, price=p, ts=t)

    def price_at(self, ts: float) -> float:
        return self.at(ts)

    def resolve(self, window: Window) -> Resolution:
        """Résolution lue sur le chemin. Aucun tirage."""
        return Resolution(window=window,
                          settle_price=self.at(window.expiry_ts),
                          settle_ts=window.expiry_ts)

    # --- fabriques -------------------------------------------------------
    @classmethod
    def gbm(cls, asset: str = "BTC", s0: float = 100_000.0,
            vol_per_s: float = 0.0004, seconds: int = 7200,
            dt: float = 1.0, drift_per_s: float = 0.0,
            seed: int = 0, t_start: float = 0.0) -> "PricePath":
        """Marche aléatoire log-normale, pour valider le pipeline de bout en bout.

        Sert de cas de contrôle, et c'est son intérêt principal : sur un GBM
        sans dérive, aucun système ne peut avoir d'edge. Si la calibration
        rend « EDGE DEMONTRE » sur ce chemin, c'est que le harnais de mesure
        est bogué, pas que la stratégie est bonne.
        """
        rng = random.Random(seed or None)
        n = max(1, int(seconds / dt))
        times, prices = [t_start], [s0]
        p = s0
        for i in range(1, n + 1):
            z = rng.gauss(0.0, 1.0)
            step = (drift_per_s - 0.5 * vol_per_s ** 2) * dt + vol_per_s * math.sqrt(dt) * z
            p *= math.exp(step)
            times.append(t_start + i * dt)
            prices.append(p)
        return cls(asset, times, prices)

    @classmethod
    def from_rows(cls, asset: str, rows, venue: str = "recorded") -> "PricePath":
        """Depuis des (ts, price) enregistrés — la voie de production."""
        pairs = sorted((float(t), float(p)) for t, p in rows)
        return cls(asset, [t for t, _ in pairs], [p for _, p in pairs], venue)


def windows_over(path: PricePath, duration_s: float = 300.0,
                 stride_s: float | None = None) -> list[Window]:
    """Découpe un chemin en fenêtres Up/Down consécutives.

    Le strike est le prix à l'ouverture de la fenêtre, convention Up/Down.
    """
    stride = stride_s or duration_s
    out: list[Window] = []
    t = path.t0
    while t + duration_s <= path.t1:
        out.append(Window(asset=path.asset, strike=path.at(t),
                          opened_ts=t, expiry_ts=t + duration_s,
                          venue=path.venue))
        t += stride
    return out
