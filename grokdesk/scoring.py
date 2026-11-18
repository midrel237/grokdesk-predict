from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ScoringMatrix:
    """The weights and thresholds RESEARCH uses to score a signal.

    AUDIT rewrites this in place from realised P&L. This object is the reason
    the desk gets smarter overnight without anyone editing a prompt by hand:
    by hour 60 these numbers are nothing like what they started as.
    """

    w_narrative: float = 0.30
    w_deployer: float = 0.30
    w_clusters: float = 0.20
    w_liquidity: float = 0.20

    min_narrative: float = 0.55
    max_cluster_overlap: float = 0.40
    min_liquidity_sol: float = 3.0
    pass_threshold: float = 0.62

    revisions: int = 0
    history: list = field(default_factory=list)

    # AUDIT can push the desk stricter, but only inside sane bounds. This is
    # what stops the self-tuning loop from strangling research into silence.
    _CLAMP = {
        "min_narrative": (0.40, 0.62),
        "max_cluster_overlap": (0.20, 0.50),
        "pass_threshold": (0.55, 0.70),
    }

    def tighten(self, note: str, **deltas: float) -> None:
        moved = False
        for k, d in deltas.items():
            lo, hi = self._CLAMP.get(k, (-1e9, 1e9))
            new = round(min(hi, max(lo, getattr(self, k) + d)), 4)
            if new != getattr(self, k):
                setattr(self, k, new)
                moved = True
        if moved:
            self.revisions += 1
            self.history.append((self.revisions, note))
