"""PRIOR — probabilité équitable qu'une fenêtre Up/Down finisse au-dessus du
strike.

C'est le prix d'une option digitale, pas un avis. Sous log-normale :

    ln(S_T/S_0) ~ N((mu - sigma^2/2) * T,  sigma^2 * T)

donc

    P(S_T > K) = Phi( (ln(S_0/K) + (mu - sigma^2/2) T) / (sigma sqrt(T)) )

`mu = 0` par défaut, et c'est un choix d'honnêteté : supposer une dérive non
nulle revient à affirmer qu'on sait prédire la direction, ce qui est
précisément l'hypothèse que le système doit tester, pas postuler.
"""
from __future__ import annotations

import math

from ..core.config import PriorConfig
from ..core.types import Fair, Snapshot, Window

_SQRT2 = math.sqrt(2.0)


def norm_cdf(x: float) -> float:
    """Phi(x) via erf de la stdlib. Pas de scipy pour une fonction scalaire."""
    return 0.5 * (1.0 + math.erf(x / _SQRT2))


def digital_p_up(spot: float, strike: float, vol_per_s: float,
                 horizon_s: float, drift_per_s: float = 0.0,
                 variance_drag: bool = True) -> float:
    """P(spot_T > strike). Robuste aux cas dégénérés.

    - horizon nul : la fenêtre est résolue, c'est 1 ou 0 (0.5 si pile au strike)
    - vol nulle : pas d'incertitude, même logique déterministe
    """
    if spot <= 0 or strike <= 0:
        raise ValueError("spot et strike doivent être > 0")
    sigma_t = vol_per_s * math.sqrt(max(0.0, horizon_s))
    if sigma_t <= 0.0:
        if spot > strike:
            return 1.0
        if spot < strike:
            return 0.0
        return 0.5
    m = math.log(spot / strike)
    mu_term = drift_per_s * horizon_s
    drag = (0.5 * vol_per_s * vol_per_s * horizon_s) if variance_drag else 0.0
    z = (m + mu_term - drag) / sigma_t
    return norm_cdf(z)


class Prior:
    """L'agent PRIOR. Sans état propre : la vol lui est fournie par SPOTTER,
    ce qui le rend trivialement testable et rejouable à l'identique."""

    def __init__(self, cfg: PriorConfig | None = None) -> None:
        self.cfg = cfg or PriorConfig()

    def fair(self, snap: Snapshot) -> Fair:
        w: Window = snap.window
        horizon = w.seconds_left(snap.now)
        spot = snap.tick.microprice
        vol = max(self.cfg.vol_floor_per_s, snap.vol_per_s)
        p_up = digital_p_up(
            spot=spot,
            strike=w.strike,
            vol_per_s=vol,
            horizon_s=horizon,
            drift_per_s=self.cfg.drift_per_s,
            variance_drag=self.cfg.use_variance_drag,
        )
        return Fair(
            window=w,
            p_up=p_up,
            vol_per_s=vol,
            horizon_s=horizon,
            log_moneyness=math.log(spot / w.strike),
            now=snap.now,
        )
