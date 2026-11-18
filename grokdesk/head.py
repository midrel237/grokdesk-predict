from __future__ import annotations


class GrokCore:
    """Agent nineteen. The head of desk.

    It never places a trade. Once an hour it reads what every desk produced
    and makes one call: who gets more budget, and who gets fired. Fired means
    AUDIT rewrites that agent from its own last window of numbers.

    It fires an agent at most once (the rewrite is supposed to fix the flaw)
    and never more than one per cooldown window, so the log reads like a desk
    cleaning up its weak seats, not a churn machine.
    """

    FIRE_BELOW = 0.40
    COOLDOWN_H = 6

    def __init__(self, desks, audit, ledger) -> None:
        self.desks = desks
        self.audit = audit
        self.ledger = ledger
        self.firings = 0
        self.decisions: list[str] = []
        self.fired: set[str] = set()
        self._last_fire_h = -99

    def _promote(self) -> None:
        roster = [a for d in self.desks for a in d.alive_agents()]
        if roster:
            best = max(roster, key=lambda a: a.score)
            best.budget = round(min(2.0, best.budget + 0.2), 2)

    def review(self, hour: int) -> str | None:
        self._promote()
        if hour - self._last_fire_h < self.COOLDOWN_H:
            return None
        pool = [a for d in self.desks for a in d.alive_agents()
                if a.desk != "TREASURY" and a.name not in self.fired]
        if not pool:
            return None
        worst = min(pool, key=lambda a: a.score)
        if worst.score >= self.FIRE_BELOW:
            return None
        reason = self._reason(worst)
        self.audit.rewrite(worst, reason)
        worst.score = 0.6
        worst.budget = 1.0
        self.fired.add(worst.name)
        self._last_fire_h = hour
        self.firings += 1
        note = f"h{hour:02d}  fired {worst.name:<9} {reason}"
        self.decisions.append(note)
        return note

    @staticmethod
    def _reason(agent) -> str:
        if agent.stats.get("stale"):
            return "polling too slow, flagging coins that already graduated"
        if agent.stats.get("loose"):
            return "scoring deployer history too loosely, same funder cluster"
        if agent.desk == "EXECUTION":
            return "redundant entries the early curve already had"
        return "underperforming on realised pnl"
