"""Recorder **DuckDB** — l'action la plus urgente du plan.

Pourquoi c'est prioritaire : il n'existe pas d'archive publique du carnet L2
Polymarket, et les flux de payouts d'un broker binaire ne sont pas archivés
non plus. La donnée de backtest ne peut venir que d'ici, et elle ne
s'accumule qu'en temps réel — chaque jour sans recorder est un jour de
backtest définitivement perdu.

Pourquoi DuckDB plutôt que SQLite : colonnaire, compression efficace sur des
snapshots de carnet très répétitifs, lecture Parquet native, et les requêtes
de backtest sont des scans analytiques sur des dizaines de millions de lignes
— exactement son terrain.

Tampon en mémoire + flush par lots : écrire tick par tick ferait du recorder
le goulot d'étranglement du pipeline.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.types import Closed, Signal, Tick

SCHEMA = """
CREATE TABLE IF NOT EXISTS ticks (
    ts        DOUBLE,
    venue     VARCHAR,
    asset     VARCHAR,
    price     DOUBLE,
    bid       DOUBLE,
    ask       DOUBLE
);
CREATE TABLE IF NOT EXISTS book_snapshots (
    ts        DOUBLE,
    venue     VARCHAR,
    symbol    VARCHAR,
    level     INTEGER,
    bid_px    DOUBLE,
    bid_qty   DOUBLE,
    ask_px    DOUBLE,
    ask_qty   DOUBLE
);
CREATE TABLE IF NOT EXISTS payouts (
    ts        DOUBLE,
    venue     VARCHAR,
    asset     VARCHAR,
    payout    DOUBLE,
    break_even DOUBLE
);
CREATE TABLE IF NOT EXISTS signals (
    ts            DOUBLE,
    asset         VARCHAR,
    side          VARCHAR,
    p             DOUBLE,
    payout        DOUBLE,
    break_even    DOUBLE,
    edge          DOUBLE,
    stake_fraction DOUBLE,
    confidence    VARCHAR,
    reasons       VARCHAR,
    strike        DOUBLE,
    expiry_ts     DOUBLE
);
CREATE TABLE IF NOT EXISTS closed (
    order_id    VARCHAR,
    asset       VARCHAR,
    side        VARCHAR,
    stake       DOUBLE,
    predicted_p DOUBLE,
    won         BOOLEAN,
    pnl         DOUBLE,
    fees        DOUBLE,
    hold_s      DOUBLE,
    strike      DOUBLE,
    settle_price DOUBLE,
    settle_ts   DOUBLE
);
CREATE TABLE IF NOT EXISTS windows (
    asset      VARCHAR,
    venue      VARCHAR,
    strike     DOUBLE,
    opened_ts  DOUBLE,
    expiry_ts  DOUBLE,
    settle_price DOUBLE,
    outcome    VARCHAR,
    p_up       DOUBLE
);
"""


class Recorder:
    """Écrit dans DuckDB si disponible, sinon accumule en mémoire.

    Le repli mémoire n'est pas de la complaisance : il permet aux tests et au
    mode hors ligne de tourner sans dépendance, avec le même code d'appel.
    """

    def __init__(self, path: str | Path = "data/market.duckdb",
                 batch_size: int = 2_000) -> None:
        self.path = Path(path)
        self.batch_size = batch_size
        self._con: Any = None
        self._buf: dict[str, list[tuple]] = {}
        self.written: dict[str, int] = {}
        self.memory_only = False

    @staticmethod
    def available() -> bool:
        try:
            import duckdb  # noqa: F401
            return True
        except ImportError:
            return False

    def open(self) -> "Recorder":
        try:
            import duckdb
        except ImportError:
            self.memory_only = True
            return self
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(self.path))
        for stmt in SCHEMA.strip().split(";"):
            if stmt.strip():
                self._con.execute(stmt)
        return self

    # --- écriture --------------------------------------------------------
    def _push(self, table: str, row: tuple) -> None:
        self._buf.setdefault(table, []).append(row)
        if len(self._buf[table]) >= self.batch_size:
            self.flush(table)

    def tick(self, t: Tick) -> None:
        self._push("ticks", (t.ts, t.venue, t.asset, t.price, t.bid, t.ask))

    def book(self, venue: str, symbol: str, ts: float,
             bids: list, asks: list, depth: int = 10) -> None:
        for i in range(depth):
            b = bids[i] if i < len(bids) else (None, None)
            a = asks[i] if i < len(asks) else (None, None)
            self._push("book_snapshots",
                       (ts, venue, symbol, i, b[0], b[1], a[0], a[1]))

    def payout(self, venue: str, asset: str, payout: float, ts: float) -> None:
        be = 1.0 / (1.0 + payout) if payout > 0 else None
        self._push("payouts", (ts, venue, asset, payout, be))

    def signal(self, s: Signal) -> None:
        self._push("signals", (
            s.now, s.window.asset, s.side.value, s.p, s.payout, s.break_even,
            s.edge, s.stake_fraction, s.confidence, "; ".join(s.reasons),
            s.window.strike, s.window.expiry_ts,
        ))

    def closed(self, c: Closed) -> None:
        o = c.order
        self._push("closed", (
            o.order_id, o.window.asset, o.side.value, o.stake, c.predicted_p,
            c.won, c.pnl, c.fees, c.hold_s, o.window.strike,
            c.resolution.settle_price, c.resolution.settle_ts,
        ))

    def window(self, w, settle_price: float, outcome: str, p_up: float) -> None:
        self._push("windows", (w.asset, w.venue, w.strike, w.opened_ts,
                               w.expiry_ts, settle_price, outcome, p_up))

    # --- flush / lecture -------------------------------------------------
    def flush(self, table: str | None = None) -> int:
        tables = [table] if table else list(self._buf)
        total = 0
        for t in tables:
            rows = self._buf.get(t) or []
            if not rows:
                continue
            if self._con is not None:
                ph = ",".join("?" * len(rows[0]))
                self._con.executemany(f"INSERT INTO {t} VALUES ({ph})", rows)
            self.written[t] = self.written.get(t, 0) + len(rows)
            total += len(rows)
            if self._con is not None:
                self._buf[t] = []
        return total

    def price_rows(self, asset: str) -> list[tuple[float, float]]:
        """Relit un chemin de prix pour alimenter `PricePath.from_rows`."""
        if self._con is None:
            return [(r[0], r[3]) for r in self._buf.get("ticks", [])
                    if r[2] == asset]
        return self._con.execute(
            "SELECT ts, price FROM ticks WHERE asset = ? ORDER BY ts", [asset]
        ).fetchall()

    def close(self) -> None:
        self.flush()
        if self._con is not None:
            self._con.close()
            self._con = None

    def __enter__(self) -> "Recorder":
        return self.open()

    def __exit__(self, *exc) -> None:
        self.close()
