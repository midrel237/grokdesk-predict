"""Payloads typés du bus.

Remplace les tuples anonymes de l'ancien `runtime.py` (`(sig, coin)`,
`(cand, coin, agent)`) : un handler ne peut plus se tromper sur la forme de ce
qu'il reçoit, et le type sert de contrat entre agents.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Side(str, Enum):
    UP = "UP"
    DOWN = "DOWN"


@dataclass(frozen=True)
class Tick:
    """Un prix observé sur un flux, avec l'horodatage de la source."""
    venue: str
    asset: str
    price: float
    ts: float
    bid: float | None = None
    ask: float | None = None

    @property
    def microprice(self) -> float:
        """Milieu pondéré si on a les deux côtés, sinon le dernier prix."""
        if self.bid is not None and self.ask is not None and self.ask > 0:
            return (self.bid + self.ask) / 2.0
        return self.price


@dataclass(frozen=True)
class Window:
    """Une fenêtre Up/Down : on compare le prix de résolution au strike."""
    asset: str
    strike: float
    opened_ts: float
    expiry_ts: float
    venue: str = "paper"

    def seconds_left(self, now: float) -> float:
        return max(0.0, self.expiry_ts - now)

    @property
    def duration_s(self) -> float:
        return self.expiry_ts - self.opened_ts


@dataclass(frozen=True)
class Snapshot:
    """Ce que publie SPOTTER. Porte l'âge de chaque source : un flux périmé
    doit faire abstenir EDGE, pas produire un signal silencieusement faux."""
    window: Window
    tick: Tick
    now: float
    vol_per_s: float
    feed_age_s: dict[str, float] = field(default_factory=dict)
    indicators: dict[str, float] = field(default_factory=dict)

    @property
    def stale(self) -> bool:
        return any(age > 2.0 for age in self.feed_age_s.values())


@dataclass(frozen=True)
class Fair:
    """Ce que publie PRIOR : une probabilité, et de quoi l'auditer."""
    window: Window
    p_up: float
    vol_per_s: float
    horizon_s: float
    log_moneyness: float
    now: float

    @property
    def p_down(self) -> float:
        return 1.0 - self.p_up


@dataclass(frozen=True)
class Signal:
    """Ce que publie EDGE : la sortie lisible du système.

    Chaque nombre ici est traçable jusqu'à sa source, ce qui est exactement ce
    qui manque à l'ancien `runtime.py` où le résultat était tiré au sort.
    """
    window: Window
    side: Side
    p: float
    payout: float
    break_even: float
    edge: float
    stake_fraction: float
    confidence: str
    reasons: tuple[str, ...]
    now: float

    def render(self) -> str:
        return (f"{self.window.asset:<8} {self.side.value:<4} "
                f"p={self.p:.3f} seuil={self.break_even:.3f} "
                f"edge={self.edge:+.4f} mise={self.stake_fraction:.3%} "
                f"[{self.confidence}] {', '.join(self.reasons)}")


@dataclass
class Order:
    """Intention d'exécution. `filled`/`rejected` sont posés par le moteur."""
    window: Window
    side: Side
    stake: float
    limit_price: float | None
    created_ts: float
    signal: Signal | None = None
    order_id: str = ""
    filled: bool = False
    rejected: str = ""
    fill_price: float | None = None
    fill_ts: float | None = None


@dataclass(frozen=True)
class Resolution:
    """Résultat réel d'une fenêtre, lu du marché. Jamais tiré au sort."""
    window: Window
    settle_price: float
    settle_ts: float

    @property
    def outcome(self) -> Side:
        return Side.UP if self.settle_price > self.window.strike else Side.DOWN


@dataclass(frozen=True)
class Closed:
    """Ce que publie CLOSER : le trade bouclé, avec son P&L réel."""
    order: Order
    resolution: Resolution
    won: bool
    pnl: float
    fees: float
    predicted_p: float
    hold_s: float          # temps marché, pas horloge murale
