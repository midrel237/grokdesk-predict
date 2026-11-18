"""KELLY — dimensionnement de la mise.

Pour un pari binaire qui rapporte `b` fois la mise en cas de gain et la perd
entièrement sinon, le Kelly complet vaut :

    f* = (b * p - (1 - p)) / b

On n'utilise jamais le Kelly complet : la fraction est plafonnée à 0,25×
(0,5× au grand maximum). La raison est dans `model/calibration.py` — Kelly
appliqué à une probabilité mal calibrée sur-mise systématiquement, et l'erreur
de sur-mise est asymétrique : elle détruit le capital plus vite que la
sous-mise ne coûte de rendement.
"""
from __future__ import annotations

from dataclasses import replace

from ..core.config import RiskConfig
from ..core.types import Signal


def kelly_full(p: float, payout: float) -> float:
    """Fraction de Kelly complète. Négative = pari défavorable."""
    if payout <= 0:
        raise ValueError("payout doit être > 0")
    q = 1.0 - p
    return (payout * p - q) / payout


class Kelly:
    def __init__(self, cfg: RiskConfig | None = None) -> None:
        self.cfg = cfg or RiskConfig()

    def size(self, sig: Signal, calibrated: bool = False) -> Signal:
        """Renseigne `stake_fraction` sur le signal.

        `calibrated=False` divise encore la mise par deux : tant que le
        rapport de calibration n'a pas rendu « EDGE DEMONTRE », on mise comme
        si on se savait optimiste, parce que c'est le cas par défaut.
        """
        f = kelly_full(sig.p, sig.payout)
        if f <= 0:
            return replace(sig, stake_fraction=0.0)
        f *= self.cfg.kelly_fraction
        if not calibrated:
            f *= 0.5
        f = min(f, self.cfg.max_stake_fraction)
        return replace(sig, stake_fraction=round(max(0.0, f), 6))
