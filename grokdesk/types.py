from __future__ import annotations

from dataclasses import dataclass, field
from time import time


@dataclass
class Signal:
    """What RADAR ships. It never carries a decision, only an observation."""
    mint: str
    symbol: str
    source: str          # the radar agent that saw it
    trend: float         # 0..1 social trend strength
    whale_flow: float    # SOL of whale buys seen on the curve
    age_s: float
    graduated: bool = False
    ts: float = field(default_factory=time)


@dataclass
class Candidate:
    """What RESEARCH ships once a signal clears all four checks."""
    signal: Signal
    scores: dict
    verdict: bool
    reason: str = ""


@dataclass
class Position:
    mint: str
    symbol: str
    size_sol: float
    entry: float
    agent: str
    tranches_out: int = 0
    ts: float = field(default_factory=time)


@dataclass
class Trade:
    mint: str
    symbol: str
    agent: str
    size_sol: float
    entry: float
    exit: float
    pnl_sol: float
    hold_s: float
    graduated: bool = False
