"""Ledger — bankroll, frais, et surveillance du drift.

Réécrit depuis l'original `grokdesk/ledger.py`. Changements de fond :

- une mise binaire est engagée puis résolue : on suit l'exposition ouverte,
  ce que l'original ne faisait pas
- les frais sont comptés séparément du P&L, pour qu'on puisse répondre à
  « combien les frais m'ont coûté » sans recalcul
- `drift()` compare le solde calculé au solde rapporté par le venue. Un écart
  silencieux est le symptôme classique d'un fill manqué ou compté deux fois.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass
class DayBook:
    day: date
    start_equity: float
    pnl: float = 0.0
    fees: float = 0.0
    n_bets: int = 0
    wins: int = 0

    @property
    def is_red(self) -> bool:
        return self.pnl < 0

    @property
    def drawdown_fraction(self) -> float:
        if self.start_equity <= 0:
            return 0.0
        return max(0.0, -self.pnl) / self.start_equity


@dataclass
class Ledger:
    start: float
    cash: float = 0.0
    exposure: float = 0.0
    fees_paid: float = 0.0
    closed: list = field(default_factory=list)
    days: dict = field(default_factory=dict)
    peak_equity: float = 0.0

    def __post_init__(self) -> None:
        self.cash = self.start
        self.peak_equity = self.start

    # --- état ------------------------------------------------------------
    @property
    def equity(self) -> float:
        return round(self.cash + self.exposure, 6)

    @property
    def wins(self) -> int:
        return sum(1 for c in self.closed if c.won)

    @property
    def losses(self) -> int:
        return sum(1 for c in self.closed if not c.won)

    @property
    def win_rate(self) -> float:
        n = len(self.closed)
        return (self.wins / n) if n else float("nan")

    @property
    def net_pnl(self) -> float:
        return round(self.equity - self.start, 6)

    @property
    def max_drawdown(self) -> float:
        """Drawdown maximal observé en fraction du pic d'equity."""
        peak, worst = self.start, 0.0
        eq = self.start
        for c in self.closed:
            eq += c.pnl - c.fees
            peak = max(peak, eq)
            if peak > 0:
                worst = max(worst, (peak - eq) / peak)
        return worst

    # --- mouvements ------------------------------------------------------
    def commit(self, stake: float, fee: float, when: date) -> None:
        """Engage une mise : le cash sort, l'exposition monte."""
        if stake < 0 or fee < 0:
            raise ValueError("mise et frais doivent être >= 0")
        if stake + fee > self.cash + 1e-9:
            raise ValueError("mise supérieure au cash disponible")
        self.cash -= stake + fee
        self.exposure += stake
        self.fees_paid += fee
        self._book(when).n_bets += 1
        self._book(when).fees += fee

    def settle(self, closed, when: date) -> None:
        """Résout une mise engagée. `closed.pnl` est net du principal."""
        self.exposure -= closed.order.stake
        self.exposure = max(0.0, round(self.exposure, 9))
        self.cash += closed.order.stake + closed.pnl
        self.closed.append(closed)
        b = self._book(when)
        b.pnl += closed.pnl
        b.wins += int(closed.won)
        self.peak_equity = max(self.peak_equity, self.equity)

    def _book(self, when: date) -> DayBook:
        if when not in self.days:
            self.days[when] = DayBook(day=when, start_equity=self.equity)
        return self.days[when]

    # --- contrôle --------------------------------------------------------
    def drift(self, venue_balance: float) -> float:
        """Écart entre notre comptabilité et celle du venue."""
        return round(venue_balance - self.equity, 6)

    def consecutive_red_days(self) -> int:
        n = 0
        for d in sorted(self.days, reverse=True):
            if self.days[d].is_red:
                n += 1
            else:
                break
        return n

    def today_drawdown(self, when: date) -> float:
        b = self.days.get(when)
        return b.drawdown_fraction if b else 0.0
