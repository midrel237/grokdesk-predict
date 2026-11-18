from __future__ import annotations

import random
import string
from dataclasses import dataclass, field

from .bonding_curve import BondingCurve

_SYLL = ["dance", "dog", "cat", "god", "frog", "moon", "pepe", "giga",
         "wif", "bonk", "chud", "turbo", "based", "mog", "shib", "goat"]


def _mint(rng: random.Random) -> str:
    body = "".join(rng.choice(string.ascii_letters + string.digits) for _ in range(6))
    return body + "pump"


@dataclass
class Coin:
    mint: str
    symbol: str
    curve: BondingCurve
    deployer: str
    funder: str          # wallet that funded the deployer; ".rug" ones cluster
    lp_burned: bool
    trend: float         # 0..1 social trend
    quality: float       # hidden ground truth: how likely it actually runs
    graduated: bool = False
    age_s: float = 0.0
    # runtime bookkeeping the desks attach as a coin moves through the pipe
    pos: object = None
    approved_by: object = None


class PumpFun:
    """Paper, read-only pump.fun feed. Emits synthetic mints and exposes
    curve state. There is no wallet and nothing is ever signed here; the
    point is to judge the desk's decisions, not to touch a chain."""

    def __init__(self, seed: int = 0) -> None:
        self.rng = random.Random(seed or None)
        # a couple of these funders are serial ruggers whose tokens share a
        # wallet cluster. RESEARCH is supposed to learn to spot them.
        self.funders = ["Fzk9-rug", "Rug7-rug", "9aPq-ok", "3mNv-ok", "Cold5-ok"]

    def new_coin(self) -> Coin:
        r = self.rng
        symbol = "$" + (r.choice(_SYLL) + r.choice(_SYLL)).upper()
        funder = r.choice(self.funders)
        is_scam = funder.endswith("rug")
        mean_q = 0.26 if is_scam else 0.5
        quality = max(0.0, min(1.0, r.gauss(mean_q, 0.2)))
        return Coin(
            mint=_mint(r),
            symbol=symbol,
            curve=BondingCurve(),
            deployer="Dev" + str(r.randint(100, 999)),
            funder=funder,
            lp_burned=r.random() > 0.25,
            trend=r.random(),
            quality=quality,
        )
