"""Adaptateur **QuantStats** — rapport de performance.

Évite d'écrire la couche de reporting (drawdown, Sharpe, distribution des
rendements, rapport HTML). QuantStats attend une série de rendements indexée
par date : on lui fournit le P&L par fenêtre, converti en rendement sur
l'equity.

Repli interne si QuantStats ou pandas sont absents, afin que les métriques de
la Phase 6 restent disponibles sans dépendance.

Avertissement de lecture : les métriques annualisées (Sharpe, CAGR) sur des
fenêtres de 5 minutes sont à prendre avec prudence — l'annualisation d'un
échantillon de quelques heures extrapole massivement. Les métriques qui
comptent ici sont le drawdown, le win rate et la distribution, pas le Sharpe.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from ..core.ledger import Ledger


@dataclass
class PerfSummary:
    n: int
    total_return: float
    win_rate: float
    max_drawdown: float
    avg_win: float
    avg_loss: float
    profit_factor: float
    expectancy: float
    fees: float

    def render(self) -> str:
        return "\n".join([
            f"  trades          : {self.n}",
            f"  rendement total : {self.total_return:+.2%}",
            f"  win rate        : {self.win_rate:.2%}",
            f"  max drawdown    : {self.max_drawdown:.2%}",
            f"  gain moyen      : {self.avg_win:+.4f}",
            f"  perte moyenne   : {self.avg_loss:+.4f}",
            f"  profit factor   : {self.profit_factor:.3f}",
            f"  espérance/trade : {self.expectancy:+.4f}",
            f"  frais payés     : {self.fees:.4f}",
        ])


def summarise(ledger: Ledger) -> PerfSummary:
    """Métriques calculées en interne, sans dépendance."""
    cl = ledger.closed
    n = len(cl)
    wins = [c.pnl for c in cl if c.won]
    losses = [c.pnl for c in cl if not c.won]
    gross_win = sum(wins)
    gross_loss = -sum(losses)
    return PerfSummary(
        n=n,
        total_return=(ledger.net_pnl / ledger.start) if ledger.start else 0.0,
        win_rate=(len(wins) / n) if n else float("nan"),
        max_drawdown=ledger.max_drawdown,
        avg_win=(gross_win / len(wins)) if wins else 0.0,
        avg_loss=(sum(losses) / len(losses)) if losses else 0.0,
        profit_factor=(gross_win / gross_loss) if gross_loss > 0 else math.inf,
        expectancy=(ledger.net_pnl / n) if n else 0.0,
        fees=ledger.fees_paid,
    )


def available() -> bool:
    try:
        import quantstats  # noqa: F401
        import pandas  # noqa: F401
        return True
    except ImportError:
        return False


def returns_series(ledger: Ledger):
    """Série de rendements par trade, indexée sur l'horodatage de règlement."""
    try:
        import pandas as pd
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("pandas absent") from e
    rows, equity = [], ledger.start
    for c in ledger.closed:
        prev = equity
        equity += c.pnl - c.fees
        rows.append((pd.to_datetime(c.resolution.settle_ts, unit="s"),
                     (equity - prev) / prev if prev else 0.0))
    if not rows:
        return pd.Series(dtype="float64")
    s = pd.Series([r for _, r in rows], index=[t for t, _ in rows])
    return s.groupby(s.index.floor("D")).apply(lambda x: (1 + x).prod() - 1)


def html_report(ledger: Ledger, out: str | Path = "data/perf.html",
                title: str = "GROK DESK — papier") -> Path | None:
    """Rapport QuantStats complet. None si la lib est absente."""
    if not available():
        return None
    import quantstats as qs
    s = returns_series(ledger)
    if s.empty:
        return None
    p = Path(out)
    p.parent.mkdir(parents=True, exist_ok=True)
    qs.reports.html(s, output=str(p), title=title)
    return p
