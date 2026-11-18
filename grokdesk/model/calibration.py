"""Calibration et verdict statistique.

Le module le plus important du projet, et celui qui n'existait pas du tout
dans la version pump.fun. Un PRIOR non calibré rend EDGE sans objet : miser
en Kelly sur une probabilité mal calibrée sur-mise systématiquement.

Contient aussi la réponse à « ai-je réellement un edge ? », avec intervalle de
confiance — pas un simple compteur de victoires.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


def brier(preds: list[float], outcomes: list[int]) -> float:
    """Erreur quadratique moyenne. 0 = parfait, 0.25 = pile ou face constant."""
    if not preds:
        return float("nan")
    return sum((p - y) ** 2 for p, y in zip(preds, outcomes)) / len(preds)


def log_loss(preds: list[float], outcomes: list[int], eps: float = 1e-12) -> float:
    if not preds:
        return float("nan")
    acc = 0.0
    for p, y in zip(preds, outcomes):
        q = min(1.0 - eps, max(eps, p))
        acc -= (y * math.log(q) + (1 - y) * math.log(1.0 - q))
    return acc / len(preds)


def brier_skill_score(preds: list[float], outcomes: list[int]) -> float:
    """Brier comparé à la référence « base rate constant ».

    > 0 : le modèle apporte de l'information. <= 0 : il n'apporte rien, et
    autant parier à la fréquence de base. C'est le test que grokdesk aurait
    échoué, son score narrative étant du bruit de hachage.
    """
    if not preds:
        return float("nan")
    base = sum(outcomes) / len(outcomes)
    ref = sum((base - y) ** 2 for y in outcomes) / len(outcomes)
    if ref == 0:
        return float("nan")
    return 1.0 - brier(preds, outcomes) / ref


@dataclass
class Bin:
    lo: float
    hi: float
    n: int = 0
    sum_p: float = 0.0
    hits: int = 0

    @property
    def mean_p(self) -> float:
        return self.sum_p / self.n if self.n else float("nan")

    @property
    def freq(self) -> float:
        return self.hits / self.n if self.n else float("nan")

    @property
    def gap(self) -> float:
        return self.freq - self.mean_p


def reliability(preds: list[float], outcomes: list[int], bins: int = 10) -> list[Bin]:
    """Courbe de fiabilité : prédit vs réalisé par tranche de probabilité.

    Si la fréquence réalisée des paris annoncés à 0.60 n'est pas ~0.60, le
    modèle est mal calibré et l'edge calculé par EDGE est fictif.
    """
    out = [Bin(i / bins, (i + 1) / bins) for i in range(bins)]
    for p, y in zip(preds, outcomes):
        idx = min(bins - 1, max(0, int(p * bins)))
        b = out[idx]
        b.n += 1
        b.sum_p += p
        b.hits += int(y)
    return out


def expected_calibration_error(preds: list[float], outcomes: list[int],
                               bins: int = 10) -> float:
    """ECE : écart |prédit - réalisé| moyen, pondéré par l'effectif."""
    if not preds:
        return float("nan")
    tot = len(preds)
    acc = 0.0
    for b in reliability(preds, outcomes, bins):
        if b.n:
            acc += (b.n / tot) * abs(b.gap)
    return acc


def wilson_interval(wins: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Intervalle de Wilson sur le taux de réussite.

    Préféré à l'intervalle normal : reste valide à petit n et ne sort jamais
    de [0, 1], ce qui compte ici parce qu'on compare à un seuil de ~0.52.
    """
    if n <= 0:
        return (0.0, 1.0)
    p = wins / n
    d = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = (z / d) * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, centre - half), min(1.0, centre + half))


def samples_needed(target_rate: float, break_even: float, z: float = 1.96) -> int:
    """Nombre de fenêtres pour distinguer `target_rate` de `break_even`.

    n >= (z * sqrt(p(1-p)) / (p - be))^2

    Avec un payout de 92 % (seuil 52.08 %) et un taux visé de 56 %, ça donne
    quelques centaines de fenêtres. C'est le coût d'entrée incontournable
    avant d'affirmer quoi que ce soit — et à 5 min par fenêtre, ça se compte
    en dizaines d'heures de fonctionnement continu.
    """
    delta = target_rate - break_even
    if delta <= 0:
        return -1  # pas d'edge à démontrer
    var = target_rate * (1.0 - target_rate)
    return int(math.ceil((z * math.sqrt(var) / delta) ** 2))


@dataclass
class CalibrationReport:
    n: int
    brier: float
    log_loss: float
    skill: float
    ece: float
    win_rate: float
    break_even: float
    ci_low: float
    ci_high: float
    n_required: int
    bins: list[Bin] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        """Le seul verdict qui autorise à envisager le live.

        Exige que la borne *basse* de l'intervalle dépasse le seuil : un win
        rate ponctuel au-dessus du seuil ne prouve rien tant que l'incertitude
        englobe encore l'absence d'edge.
        """
        if self.n < 30:
            return "INSUFFISANT"
        if self.ci_low > self.break_even:
            return "EDGE DEMONTRE"
        if self.ci_high < self.break_even:
            return "PERDANT DEMONTRE"
        return "INDETERMINE"

    def render(self) -> str:
        req = "n/a" if self.n_required < 0 else str(self.n_required)
        return "\n".join([
            f"  fenêtres        : {self.n}  (requis ≈ {req})",
            f"  win rate        : {self.win_rate:.4f}  "
            f"IC95 [{self.ci_low:.4f}, {self.ci_high:.4f}]",
            f"  seuil équilibre : {self.break_even:.4f}",
            f"  Brier           : {self.brier:.4f}   skill {self.skill:+.4f}",
            f"  log loss        : {self.log_loss:.4f}",
            f"  ECE             : {self.ece:.4f}",
            f"  VERDICT         : {self.verdict}",
        ])


class Calibrator:
    """Accumule les prédictions et rend le rapport. Alimenté par CLOSER."""

    def __init__(self) -> None:
        self.preds: list[float] = []
        self.outcomes: list[int] = []
        self.bets: list[tuple[float, int]] = []   # (p annoncé, gagné)
        self.payouts: list[float] = []

    def record_forecast(self, p_up: float, went_up: bool) -> None:
        """Toute fenêtre observée, qu'on ait parié ou non. Mesure le modèle."""
        self.preds.append(p_up)
        self.outcomes.append(int(went_up))

    def record_bet(self, p: float, won: bool, payout: float) -> None:
        """Seulement les fenêtres jouées. Mesure le système complet."""
        self.bets.append((p, int(won)))
        self.payouts.append(payout)

    def report(self, assumed_payout: float | None = None) -> CalibrationReport:
        payout = assumed_payout
        if payout is None:
            payout = (sum(self.payouts) / len(self.payouts)) if self.payouts else 0.92
        be = 1.0 / (1.0 + payout)
        wins = sum(y for _, y in self.bets)
        n_bets = len(self.bets)
        lo, hi = wilson_interval(wins, n_bets)
        target = max(be + 0.04, (wins / n_bets) if n_bets else be + 0.04)
        return CalibrationReport(
            n=n_bets,
            brier=brier(self.preds, self.outcomes),
            log_loss=log_loss(self.preds, self.outcomes),
            skill=brier_skill_score(self.preds, self.outcomes),
            ece=expected_calibration_error(self.preds, self.outcomes),
            win_rate=(wins / n_bets) if n_bets else float("nan"),
            break_even=be,
            ci_low=lo,
            ci_high=hi,
            n_required=samples_needed(target, be),
            bins=reliability(self.preds, self.outcomes),
        )
