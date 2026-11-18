"""Risk engine et kill switch.

Hérite des caps de `desks/risk.py` et ajoute ce qui manquait : arrêt sur
drawdown journalier, pause après N journées rouges, surveillance du drift, et
un gel qui ne peut être levé que par un appel explicite et tracé.

Corrige aussi un défaut de l'original : le HUD y affichait `[FROZEN]` dès que
le wallet passait sous le capital de départ, alors que le vrai gel était à
60 %. Un indicateur de risque qui ment est pire que pas d'indicateur. Ici il
n'existe qu'une seule source de vérité, `state()`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from ..core.config import RiskConfig
from ..core.ledger import Ledger
from ..core.types import Signal


@dataclass
class Block:
    reason: str
    detail: str = ""


@dataclass
class RiskEngine:
    ledger: Ledger
    cfg: RiskConfig = field(default_factory=RiskConfig)
    open_positions: int = 0
    entries_this_hour: int = 0
    _frozen: bool = False
    _freeze_reasons: list = field(default_factory=list)
    blocks: dict = field(default_factory=dict)

    # --- gel -------------------------------------------------------------
    @property
    def frozen(self) -> bool:
        return self._frozen

    def freeze(self, reason: str) -> None:
        """Gèle le desk. Idempotent, et chaque motif est conservé."""
        self._frozen = True
        if reason not in self._freeze_reasons:
            self._freeze_reasons.append(reason)

    def unfreeze(self, authorised_by: str) -> None:
        """Dégel explicite et attribué.

        Volontairement non appelable par le pipeline d'agents : aucun agent
        n'a de référence vers cette méthode dans le wiring de `runtime.py`.
        Seul un opérateur humain, via le dashboard ou la CLI, la déclenche.
        """
        if authorised_by != "operator":
            raise PermissionError(
                "seul l'opérateur peut lever un gel "
                f"(tentative par {authorised_by!r})"
            )
        self._frozen = False
        self._freeze_reasons.clear()

    @property
    def freeze_reasons(self) -> tuple[str, ...]:
        return tuple(self._freeze_reasons)

    # --- surveillance continue -------------------------------------------
    def check_kill_switch(self, today: date, venue_balance: float | None = None) -> None:
        """À appeler à chaque boucle. Gèle si une limite est franchie."""
        dd = self.ledger.today_drawdown(today)
        if dd >= self.cfg.daily_drawdown_stop:
            self.freeze(f"drawdown journalier {dd:.2%} "
                        f">= {self.cfg.daily_drawdown_stop:.2%}")
        red = self.ledger.consecutive_red_days()
        if red >= self.cfg.consecutive_red_days_stop:
            self.freeze(f"{red} journées rouges consécutives")
        if venue_balance is not None:
            drift = abs(self.ledger.drift(venue_balance))
            tol = self.cfg.ledger_drift_tolerance * max(1.0, self.ledger.start)
            if drift > tol:
                self.freeze(f"drift ledger {drift:.4f} > tolérance {tol:.4f}")

    # --- autorisation ----------------------------------------------------
    def _block(self, reason: str, detail: str = "") -> Block:
        self.blocks[reason] = self.blocks.get(reason, 0) + 1
        return Block(reason, detail)

    def allow(self, sig: Signal, today: date) -> Block | None:
        """None = autorisé. Sinon le Block porte le motif du refus."""
        if self._frozen:
            return self._block("gele", "; ".join(self._freeze_reasons))
        if self.open_positions >= self.cfg.max_open_positions:
            return self._block("trop_de_positions",
                               f"{self.open_positions}/{self.cfg.max_open_positions}")
        if self.entries_this_hour >= self.cfg.max_entries_per_hour:
            return self._block("quota_horaire",
                               f"{self.entries_this_hour}/{self.cfg.max_entries_per_hour}")
        if sig.stake_fraction <= 0:
            return self._block("mise_nulle")
        if sig.stake_fraction > self.cfg.max_stake_fraction + 1e-9:
            return self._block("mise_hors_cap",
                               f"{sig.stake_fraction:.4f} > {self.cfg.max_stake_fraction:.4f}")
        stake = self.stake_for(sig)
        if stake <= 0 or stake > self.ledger.cash:
            return self._block("cash_insuffisant",
                               f"besoin {stake:.4f}, dispo {self.ledger.cash:.4f}")
        return None

    def stake_for(self, sig: Signal) -> float:
        """Mise en unités de compte, plafonnée par le cap dur."""
        frac = min(sig.stake_fraction, self.cfg.max_stake_fraction)
        return round(self.ledger.equity * frac, 6)

    def state(self) -> dict:
        """Source unique de vérité pour le HUD et le dashboard."""
        return {
            "frozen": self._frozen,
            "freeze_reasons": list(self._freeze_reasons),
            "open_positions": self.open_positions,
            "entries_this_hour": self.entries_this_hour,
            "blocks": dict(self.blocks),
        }
