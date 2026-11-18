"""AUDIT — note le système et réécrit les seuils depuis le réalisé.

Conserve le mécanisme de l'original `desks/audit.py` (durcir la matrice depuis
le P&L), mais la note change de nature : au lieu d'un nudge ±0,05 arbitraire,
AUDIT regarde l'écart entre edge prédit et edge réalisé, et la calibration.

C'est aussi lui qui répond à la seule question qui compte avant d'envisager
l'argent réel : « l'edge est-il démontré, ou seulement espéré ? »
"""
from __future__ import annotations

from ..core.bus import Bus
from ..core.scoring import ScoringMatrix
from ..core.types import Closed
from ..model.calibration import Calibrator, CalibrationReport


class Audit:
    def __init__(self, bus: Bus, matrix: ScoringMatrix,
                 calibrator: Calibrator, review_every: int = 50) -> None:
        self.bus = bus
        self.m = matrix
        self.cal = calibrator
        self.review_every = review_every
        self.log: list[str] = []
        self._since_review = 0

    async def on_closed(self, c: Closed) -> None:
        self._since_review += 1
        if self._since_review < self.review_every:
            return
        self._since_review = 0
        self.review()

    def review(self) -> CalibrationReport:
        """Durcit ou relâche les seuils selon ce que dit le réalisé."""
        rep = self.cal.report()
        if rep.n < 30:
            return rep

        realised_edge = rep.win_rate - rep.break_even
        # edge prédit moyen sur les paris effectivement joués
        predicted = ([p for p, _ in self.cal.bets])
        predicted_edge = (sum(predicted) / len(predicted) - rep.break_even
                          if predicted else 0.0)

        # le modèle promet plus qu'il ne livre → on exige plus d'edge
        if predicted_edge - realised_edge > 0.02:
            if self.m.tighten(
                f"edge prédit {predicted_edge:+.4f} vs réalisé "
                f"{realised_edge:+.4f} sur n={rep.n}",
                min_edge=+0.005, min_confidence_p=+0.005,
            ):
                self.log.append(f"rev{self.m.revisions}: durcissement "
                                f"(sur-promesse de {predicted_edge - realised_edge:+.4f})")

        # mal calibré → exiger l'accord des indicateurs comme garde-fou
        if rep.ece > 0.05 and not self.m.require_indicator_agreement:
            self.m.require_indicator_agreement = True
            self.m.revisions += 1
            self.log.append(f"rev{self.m.revisions}: accord indicateurs exigé "
                            f"(ECE {rep.ece:.4f})")

        # edge démontré et calibration saine → on peut relâcher un cran
        if rep.verdict == "EDGE DEMONTRE" and rep.ece < 0.03:
            if self.m.tighten(f"edge démontré sur n={rep.n}", min_edge=-0.002):
                self.log.append(f"rev{self.m.revisions}: relâchement contrôlé")
        return rep

    def gate_live(self) -> tuple[bool, str]:
        """Le verrou avant l'argent réel. Exige un edge *démontré*, pas un
        P&L positif : un P&L positif sur 40 fenêtres n'est pas une preuve."""
        rep = self.cal.report()
        if rep.verdict != "EDGE DEMONTRE":
            return False, f"verdict={rep.verdict} sur n={rep.n}"
        if rep.ece > 0.05:
            return False, f"calibration insuffisante (ECE {rep.ece:.4f})"
        if rep.skill <= 0:
            return False, f"aucun apport informationnel (skill {rep.skill:+.4f})"
        return True, (f"edge démontré: IC95 bas {rep.ci_low:.4f} > "
                      f"seuil {rep.break_even:.4f} sur n={rep.n}")
