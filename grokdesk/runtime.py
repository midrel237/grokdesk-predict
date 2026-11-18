from __future__ import annotations

import asyncio
import random

from .bus import Bus
from .head import GrokCore
from .ledger import Ledger
from .llm import LLM
from .scoring import ScoringMatrix
from .desks.audit import Audit
from .desks.base import Agent
from .desks.execution import Execution
from .desks.radar import Radar
from .desks.research import Research
from .desks.risk import Risk
from .desks.treasury import Treasury
from .market.broker import PaperBroker
from .market.pumpfun import PumpFun

BANNER = r"""
  ____ ____   ___  _  __  ____  _____ ____  _  __
 / ___|  _ \ / _ \| |/ / |  _ \| ____/ ___|| |/ /
| |  _| |_) | | | | ' /  | | | |  _| \___ \| ' /
| |_| |  _ <| |_| | . \  | |_| | |___ ___) | . \
 \____|_| \_\\___/|_|\_\ |____/|_____|____/|_|\_\
   six desks · one head · nineteen agents · pump.fun
"""


def build(start_sol: float = 5.0, seed: int = 0) -> dict:
    bus = Bus()
    llm = LLM()
    matrix = ScoringMatrix()
    ledger = Ledger(start_sol)
    broker = PaperBroker(ledger)
    pf = PumpFun(seed)

    radar_agents = [Agent("SCOUT", "RADAR"), Agent("FEED", "RADAR"), Agent("SIGNAL", "RADAR")]
    poll = {"SCOUT": 3, "FEED": 8, "SIGNAL": 5}          # FEED polls too slow on purpose

    research_agents = [Agent("MEMORY", "RESEARCH"), Agent("MEMORY-2", "RESEARCH")]
    research_agents[1].stats["loose"] = True             # the one that gets fired

    exec_agents = [Agent("EXEC", "EXECUTION"), Agent("SNIPER", "EXECUTION"), Agent("ROUTER", "EXECUTION")]
    exec_agents += [Agent(f"AGENT {i:02d}", "EXECUTION") for i in range(1, 8)]

    risk_agents = [Agent("RISK", "RISK")]
    audit_agents = [Agent("HEDGE", "AUDIT"), Agent("AUDIT-2", "AUDIT")]
    treasury_agents = [Agent("VAULT", "TREASURY")]

    risk = Risk(bus, risk_agents, broker, ledger)
    radar = Radar(bus, radar_agents, poll)
    research = Research(bus, research_agents, matrix, llm)
    execution = Execution(bus, exec_agents, broker, risk)
    audit = Audit(bus, audit_agents, matrix)
    treasury = Treasury(bus, treasury_agents, ledger, risk)
    desks = [radar, research, execution, risk, audit, treasury]
    head = GrokCore(desks, audit, ledger)

    bus.on("signal", research.on_signal)
    bus.on("candidate", execution.on_candidate)
    bus.on("close", audit.on_close)

    return {
        "bus": bus, "pf": pf, "broker": broker, "ledger": ledger, "matrix": matrix,
        "radar": radar, "research": research, "execution": execution, "risk": risk,
        "audit": audit, "treasury": treasury, "head": head, "desks": desks,
    }


def _hud(hour, led, c, head, note, swept):
    line = (f"h{hour:02d}  scan {c['scanned']:>6}  research {c['passed']:>5}  "
            f"entries {c['entries']:>4}  W/L {led.wins}/{led.losses}  grad {c['grads']:>2}  "
            f"wallet {led.wallet_sol:7.2f}  vault {led.vault_sol:7.2f}  net {led.net_sol:7.2f} SOL")
    if led.wallet_sol < led.start_sol:
        line += "  [FROZEN]"
    if swept:
        line += f"  swept {swept:.2f}->cold"
    if note:
        line += f"\n      >> {note}"
    return line


async def run(hours: int = 72, tph: int = 40, start_sol: float = 5.0,
              seed: int = 0, live: bool = True, delay: float = 0.0) -> dict:
    s = build(start_sol, seed)
    bus, pf, broker, ledger = s["bus"], s["pf"], s["broker"], s["ledger"]
    radar, execution, head, treasury = s["radar"], s["execution"], s["head"], s["treasury"]
    risk = s["risk"]
    rng = random.Random(seed or 7)

    coins: list = []
    plan: dict[str, dict] = {}          # mint -> exit plan for open positions
    counters = {"scanned": 0, "passed": 0, "entries": 0, "grads": 0}

    async def on_signal(_):
        counters["scanned"] += 1        # every radar observation forwarded

    async def on_candidate(_):
        counters["passed"] += 1         # cleared all four research checks

    async def on_entry(payload):
        coin, pos, trader = payload
        counters["entries"] += 1
        q = coin.quality
        is_rug = coin.funder.endswith("rug")
        win = rng.random() < (0.12 if is_rug else 0.30 + 0.5 * q)
        if win:
            mult = round(2.0 + 3.6 * q + rng.random(), 3)     # a runner
        else:
            mult = round(rng.uniform(0.25, 0.85), 3)          # cut it
        plan[coin.mint] = {"ticks": 0, "max": rng.randint(5, 14),
                           "mult": mult, "win": win}

    bus.on("signal", on_signal)
    bus.on("candidate", on_candidate)
    bus.on("entry", on_entry)

    if live:
        print(BANNER)
        print(f"start {start_sol:.1f} SOL · {hours}h · 0 human hands\n")

    for tick in range(hours * tph):
        if tick % tph == 0:
            risk.entries_this_hour = 0     # fresh entry budget each hour
        # a burst of fresh mints every tick
        for _ in range(rng.randint(2, 5)):
            coins.append(pf.new_coin())

        # age coins; some high-quality ones graduate in the wild, which is how
        # a slow radar agent ends up flagging things that already ran
        for c in coins:
            c.age_s += 3
            if not c.graduated and c.age_s > 30 and rng.random() < c.quality * 0.04:
                c.graduated = True

        # radar re-scans a rolling window; each look is a "signal scanned"
        for c in coins[-10:]:
            await radar.observe(c, tick)

        # manage open positions: ROUTER ladders out once target or timeout hits
        for mint, p in list(plan.items()):
            pos = broker.open.get(mint)
            if pos is None:
                plan.pop(mint, None)
                continue
            coin = next((x for x in coins if x.mint == mint), None)
            if coin is None:
                continue
            p["ticks"] += 1
            done = (p["win"] and p["ticks"] >= 3) or p["ticks"] >= p["max"]
            if not done:
                continue
            if p["win"] and p["mult"] >= 3.0:
                coin.graduated = True
            trade = broker.settle(coin, pos, p["mult"])
            if coin.graduated:
                counters["grads"] += 1
            trader = execution.agents.get(pos.agent)
            if trader:
                trader.credit(trade.pnl_sol > 0, 0.12)
            await bus.emit("close", (coin, trade))
            plan.pop(mint, None)

        # end of hour: head reviews the roster, treasury sweeps and guards
        if tick % tph == tph - 1:
            hour = tick // tph + 1
            note = head.review(hour)
            swept = treasury.tick_hour(hour)
            if live:
                print(_hud(hour, ledger, counters, head, note, swept))

        if len(coins) > 120:
            coins = coins[-120:]
        if delay:
            await asyncio.sleep(delay)

    _summary(s, counters)
    return {"state": s, "counters": counters}


def _summary(s, counters) -> None:
    led, head, audit, matrix = s["ledger"], s["head"], s["audit"], s["matrix"]
    print("\n" + "=" * 60)
    print("72 HOUR SCOREBOARD  (straight off the vault)")
    print("=" * 60)
    print(f"  signals scanned : {counters['scanned']:>8}")
    print(f"  cleared research: {counters['passed']:>8}")
    print(f"  entries taken   : {counters['entries']:>8}")
    print(f"  wins / losses   : {led.wins} / {led.losses}   (loss cost {led.loss_cost:.2f} SOL)")
    print(f"  graduations     : {counters['grads']:>8}")
    print(f"  net             : {led.start_sol:.1f} SOL -> {led.net_sol:.2f} SOL")
    print(f"  matrix revisions: {matrix.revisions}   (research is not running its day-one config)")
    print(f"\n  firings ({head.firings}):")
    for name, reason, rev in audit.log:
        print(f"    - {name}: {reason}  [prompt rev {rev}]")
    print("\n  human approval adds 4.2s of latency per entry. recommend removing.")
    print("  (left unapproved.)\n")
