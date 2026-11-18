"""EDGE — convertit une probabilité en signal, ou s'abstient.

Le coeur du système, et sa formule tient en une ligne :

    seuil_equilibre = 1 / (1 + payout)
    edge            = p - seuil_equilibre

Le payout *est* le seuil. Sur un binaire à 92 % il faut 52,08 % de réussite
pour ne rien gagner ; à 80 % il en faut 55,56 %. Comme le payout est fixé par
la contrepartie et varie par actif, le seuil est lu en direct et jamais
supposé.

EDGE s'abstient plus souvent qu'il n'émet, et c'est le comportement voulu.
"""
from __future__ import annotations

from ..core.config import EdgeConfig
from ..core.scoring import ScoringMatrix
from ..core.types import Fair, Side, Signal, Snapshot


def break_even(payout: float) -> float:
    """Taux de réussite d'équilibre pour un payout donné (0.92 = 92 %)."""
    if payout <= 0:
        raise ValueError("payout doit être > 0")
    return 1.0 / (1.0 + payout)


class Edge:
    def __init__(self, cfg: EdgeConfig | None = None,
                 matrix: ScoringMatrix | None = None) -> None:
        self.cfg = cfg or EdgeConfig()
        self.m = matrix or ScoringMatrix()
        self.emitted = 0
        self.abstained = 0
        self.abstain_reasons: dict[str, int] = {}

    def _abstain(self, reason: str) -> None:
        self.abstained += 1
        self.abstain_reasons[reason] = self.abstain_reasons.get(reason, 0) + 1

    def evaluate(self, fair: Fair, snap: Snapshot, payout: float) -> Signal | None:
        """Renvoie un Signal, ou None avec la raison de l'abstention comptée."""
        # --- portes de recevabilité, avant tout calcul d'edge -------------
        if payout < self.cfg.min_payout:
            self._abstain("payout_trop_bas")
            return None
        if snap.stale:
            # un prix spot vieux de quelques secondes sur une fenêtre de 5 min
            # est une information fausse, pas une information dégradée
            self._abstain("flux_perime")
            return None
        if fair.horizon_s < self.cfg.min_horizon_s:
            self._abstain("horizon_trop_court")
            return None
        if fair.horizon_s > self.cfg.max_horizon_s:
            self._abstain("horizon_trop_long")
            return None

        be = break_even(payout)
        # --- le côté qui porte l'edge, s'il y en a un ---------------------
        if fair.p_up >= fair.p_down:
            side, p = Side.UP, fair.p_up
        else:
            side, p = Side.DOWN, fair.p_down
        edge = p - be

        threshold = max(self.cfg.min_edge, self.m.min_edge)
        if edge < threshold:
            self._abstain("edge_insuffisant")
            return None

        reasons = [f"p={p:.3f}>seuil={be:.3f}"]
        net = snap.indicators.get("net_power")
        if net is not None and snap.indicators.get("elder_ready", 0.0) >= 1.0:
            aligned = (net > 0) if side is Side.UP else (net < 0)
            if not aligned and self.m.require_indicator_agreement:
                self._abstain("net_power_contraire")
                return None
            reasons.append("net_power " + ("aligné" if aligned else "ignoré"))

        imb = snap.indicators.get("book_imbalance")
        if imb is not None:
            reasons.append(f"imbalance={imb:+.2f}")

        conf = ("forte" if edge >= threshold * 2 else "faible")
        self.emitted += 1
        return Signal(
            window=fair.window,
            side=side,
            p=p,
            payout=payout,
            break_even=be,
            edge=edge,
            stake_fraction=0.0,     # rempli par KELLY
            confidence=conf,
            reasons=tuple(reasons),
            now=fair.now,
        )
