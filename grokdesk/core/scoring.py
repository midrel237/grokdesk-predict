"""Matrice de scoring auto-ajustée — héritée de l'original `grokdesk/scoring.py`.

C'était la meilleure idée du projet pump.fun et elle se transpose telle quelle :
AUDIT durcit les seuils depuis le P&L réalisé, mais seulement dans des bornes
`_CLAMP`. C'est ce qui empêche la boucle d'auto-tuning d'étrangler EDGE
jusqu'au silence, ou au contraire de le relâcher jusqu'à tout accepter.

Deux ajouts par rapport à l'original : les seuils sont ceux d'un marché
binaire (edge minimum, accord des indicateurs), et l'état est sérialisable —
l'original perdait tout son historique à la fin du process.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class ScoringMatrix:
    min_edge: float = 0.02
    min_confidence_p: float = 0.55
    max_vol_ratio: float = 2.5
    require_indicator_agreement: bool = False

    revisions: int = 0
    history: list = field(default_factory=list)

    _CLAMP = {
        "min_edge": (0.010, 0.080),
        "min_confidence_p": (0.520, 0.700),
        "max_vol_ratio": (1.500, 4.000),
    }

    def tighten(self, note: str, **deltas: float) -> bool:
        """Applique les deltas en restant dans les bornes. True si ça a bougé."""
        moved = False
        for k, d in deltas.items():
            if k not in self._CLAMP:
                raise KeyError(f"{k} n'est pas un seuil ajustable")
            lo, hi = self._CLAMP[k]
            cur = getattr(self, k)
            new = round(min(hi, max(lo, cur + d)), 6)
            if new != cur:
                setattr(self, k, new)
                moved = True
        if moved:
            self.revisions += 1
            self.history.append({"rev": self.revisions, "note": note,
                                 "state": self.snapshot()})
        return moved

    def snapshot(self) -> dict:
        return {k: getattr(self, k) for k in
                ("min_edge", "min_confidence_p", "max_vol_ratio",
                 "require_indicator_agreement")}

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(asdict(self), indent=2),
                              encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "ScoringMatrix":
        p = Path(path)
        if not p.exists():
            return cls()
        data = json.loads(p.read_text(encoding="utf-8"))
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})
