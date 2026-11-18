from __future__ import annotations

from .base import Desk
from ..types import Candidate


class Research(Desk):
    """Desk 2. MEMORY scores every signal on four checks: narrative strength,
    deployer history, wallet clusters and liquidity shape. Pass all four and
    the aggregate threshold, or the signal dies here. Most of them die here.

    One research agent starts out grading deployers too loosely (stats['loose']).
    When its approvals keep losing on the same funder wallet, the head fires it
    and AUDIT tightens the cluster check.
    """

    name = "RESEARCH"

    def __init__(self, bus, agents, matrix, llm) -> None:
        super().__init__(bus, agents)
        self.m = matrix
        self.llm = llm
        self._rr = 0

    async def on_signal(self, payload) -> None:
        sig, coin = payload
        agents = self.alive_agents()
        if not agents:
            return
        agent = agents[self._rr % len(agents)]
        self._rr += 1
        agent.stats["seen"] = agent.stats.get("seen", 0) + 1

        narrative = self.llm.score_narrative(coin.symbol, coin.deployer)
        is_rug_funder = coin.funder.endswith("rug")
        deployer_ok = not is_rug_funder
        cluster_overlap = 0.7 if is_rug_funder else 0.1
        liq = coin.curve.real_sol

        scores = {
            "narrative": round(narrative, 3),
            "deployer": 1.0 if deployer_ok else 0.0,
            "clusters": round(1.0 - cluster_overlap, 3),
            "liquidity": round(min(1.0, liq / max(0.1, self.m.min_liquidity_sol)), 3),
        }
        agg = (scores["narrative"] * self.m.w_narrative
               + scores["deployer"] * self.m.w_deployer
               + scores["clusters"] * self.m.w_clusters
               + scores["liquidity"] * self.m.w_liquidity)

        # a "loose" agent skips the deployer gate and clears a lower bar, so
        # it waves weak names through. that is the flaw it gets fired for.
        loose = agent.stats.get("loose", False)
        deployer_gate = deployer_ok or loose
        bar = self.m.pass_threshold - (0.14 if loose else 0.0)
        checks = (narrative >= self.m.min_narrative
                  and deployer_gate
                  and cluster_overlap <= self.m.max_cluster_overlap)
        verdict = bool(checks and agg >= bar)

        cand = Candidate(sig, scores, verdict, "pass" if verdict else "killed")
        if verdict:
            await self.bus.emit("candidate", (cand, coin, agent))
        else:
            agent.stats["killed"] = agent.stats.get("killed", 0) + 1
