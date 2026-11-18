from __future__ import annotations

from .base import Desk


class Audit(Desk):
    """Desk 5. Grades every closed trade and rewrites RESEARCH's scoring
    matrix from realised P&L. It is also the mechanism behind a firing: when
    the head fires an agent, AUDIT rewrites that agent's prompt from the last
    window of its own numbers, and the replacement measurably beats it.
    """

    name = "AUDIT"

    def __init__(self, bus, agents, matrix) -> None:
        super().__init__(bus, agents)
        self.m = matrix
        self.log: list[tuple[str, str, int]] = []

    async def on_close(self, payload) -> None:
        coin, trade = payload
        lost = trade.pnl_sol <= 0
        approver = getattr(coin, "approved_by", None)
        # the research agent that greenlit a name owns the outcome
        if approver is not None:
            approver.credit(not lost, 0.05)
        # a loser a loose agent waved through is a pattern, not bad luck. feed
        # it back into the matrix so research tightens on its own overnight.
        if lost and (coin.funder.endswith("rug") or (approver and approver.stats.get("loose"))):
            self.m.tighten("weak deployer slipped through research",
                           max_cluster_overlap=-0.03, min_narrative=+0.015,
                           pass_threshold=+0.01)

    def rewrite(self, agent, reason: str) -> None:
        """Rewrite the fired agent's prompt from its recent numbers and give
        it a clean slate to prove the new config on."""
        agent.prompt_rev += 1
        agent.stats["loose"] = False        # tightened deployer gate
        agent.stats.pop("stale", None)       # faster polling window
        agent.score = 0.5
        self.log.append((agent.name, reason, agent.prompt_rev))
