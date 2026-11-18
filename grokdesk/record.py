"""Recorder 24/7. `python -m grokdesk.record --assets btc,eth,sol`

L'action la plus urgente du plan, et la raison est calendaire : il n'existe pas
d'archive publique du carnet L2 Polymarket, donc la donnée de backtest ne peut
venir que d'ici et ne s'accumule qu'en temps réel. Chaque jour sans recorder
est un jour de backtest définitivement perdu.

Ce qu'il archive, par fenêtre de 5 minutes et par actif :
  * le carnet des deux jambes (Up / Down), profondeur configurable
  * le payout implicite et le seuil d'équilibre qui en découle
  * la résolution de la fenêtre, une fois passée

Tourne en stdlib pure si DuckDB est absent (archivage en mémoire, utile pour
un test de bout en bout) ; avec DuckDB, écrit par lots dans data/market.duckdb.
"""
from __future__ import annotations

import argparse
import signal
import time
from dataclasses import dataclass, field

from .feeds.polymarket import (Book, Polymarket, UpDownMarket,
                               current_window_start, WINDOW_S)
from .store.recorder import Recorder


@dataclass
class Stats:
    started: float = field(default_factory=time.time)
    polls: int = 0
    books: int = 0
    quotes: int = 0
    errors: int = 0
    windows_seen: set = field(default_factory=set)

    @property
    def uptime_s(self) -> float:
        return time.time() - self.started

    def line(self) -> str:
        h, rem = divmod(int(self.uptime_s), 3600)
        m, s = divmod(rem, 60)
        return (f"  uptime {h:02d}:{m:02d}:{s:02d}  polls {self.polls:>6}  "
                f"carnets {self.books:>7}  fenêtres {len(self.windows_seen):>5}  "
                f"erreurs {self.errors:>4}")


class MarketRecorder:
    def __init__(self, assets: tuple[str, ...], rec: Recorder,
                 depth: int = 10, poll_s: float = 5.0) -> None:
        self.assets = assets
        self.rec = rec
        self.depth = depth
        self.poll_s = poll_s
        self.pm = Polymarket()
        self.stats = Stats()
        self._stop = False
        # fenêtres vues, pour archiver leur résolution quand elles expirent
        self._pending: dict[str, UpDownMarket] = {}

    def stop(self, *_) -> None:
        self._stop = True

    def _record_book(self, m: UpDownMarket, leg: str, b: Book) -> None:
        symbol = f"{m.slug}:{leg}"
        self.rec.book("polymarket", symbol, b.ts, b.bids, b.asks, self.depth)
        self.stats.books += 1
        payout = b.implied_payout
        if payout is not None:
            # le payout implicite est la donnée qui fixe le seuil d'EDGE :
            # on l'archive comme une série à part entière
            self.rec.payout("polymarket", f"{m.asset}:{leg}", payout, b.ts)

    def poll_once(self) -> None:
        self.stats.polls += 1
        now = time.time()
        for asset in self.assets:
            try:
                markets = self.pm.upcoming(asset, ahead=1, now=now)
            except Exception:
                self.stats.errors += 1
                continue
            for m in markets:
                self._pending.setdefault(m.slug, m)
                self.stats.windows_seen.add(m.slug)
                for leg, tok in (("up", m.token_up), ("down", m.token_down)):
                    try:
                        self._record_book(m, leg, self.pm.book(tok))
                    except Exception:
                        self.stats.errors += 1
        self._settle_expired(now)

    def _settle_expired(self, now: float) -> None:
        """Archive la résolution des fenêtres expirées depuis plus d'une minute."""
        done = [s for s, m in self._pending.items()
                if m.expiry_unix + 60 < now]
        for slug in done:
            m = self._pending.pop(slug)
            try:
                fresh = self.pm.market(m.asset, m.start_unix)
            except Exception:
                self.stats.errors += 1
                continue
            if fresh is None:
                continue
            try:
                up = self.pm.book(fresh.token_up)
            except Exception:
                self.stats.errors += 1
                continue
            # après résolution, la jambe gagnante cote ~1 et la perdante ~0
            mid = up.mid
            outcome = "UP" if (mid is not None and mid > 0.5) else "DOWN"
            from .core.types import Window
            w = Window(asset=m.asset, strike=0.0,
                       opened_ts=float(m.start_unix),
                       expiry_ts=float(m.expiry_unix), venue="polymarket")
            self.rec.window(w, mid if mid is not None else -1.0, outcome, -1.0)

    def run(self, max_seconds: float | None = None,
            quiet: bool = False) -> Stats:
        signal.signal(signal.SIGINT, self.stop)
        try:
            signal.signal(signal.SIGTERM, self.stop)
        except (AttributeError, ValueError):
            pass
        last_hud = 0.0
        while not self._stop:
            t0 = time.time()
            if max_seconds is not None and self.stats.uptime_s >= max_seconds:
                break
            self.poll_once()
            self.rec.flush()
            if not quiet and time.time() - last_hud >= 15:
                last_hud = time.time()
                print(self.stats.line(), flush=True)
            slept = time.time() - t0
            if (rest := self.poll_s - slept) > 0:
                time.sleep(rest)
        self.rec.flush()
        return self.stats


def main() -> None:
    ap = argparse.ArgumentParser(
        prog="grokdesk.record",
        description="Archive le carnet et les payouts Polymarket Up/Down (24/7).")
    ap.add_argument("--assets", default="btc,eth,sol")
    ap.add_argument("--depth", type=int, default=10,
                    help="niveaux de carnet archivés par côté")
    ap.add_argument("--poll", type=float, default=5.0,
                    help="secondes entre deux relevés")
    ap.add_argument("--db", default="data/parquet",
                    help="répertoire des partitions Parquet, ou fichier .duckdb "
                         "avec --format duckdb")
    ap.add_argument("--format", choices=["parquet", "duckdb"],
                    default="parquet",
                    help="parquet (défaut) : lecture possible PENDANT "
                         "l'enregistrement. duckdb : verrou exclusif.")
    ap.add_argument("--seconds", type=float, default=None,
                    help="s'arrêter après N secondes (défaut : jusqu'à Ctrl-C)")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    assets = tuple(a.strip() for a in args.assets.split(",") if a.strip())
    rec = Recorder(args.db, fmt=args.format).open()
    if rec.memory_only:
        print("  duckdb absent : archivage en MEMOIRE seulement "
              "(rien ne sera conservé)")
    mr = MarketRecorder(assets, rec, depth=args.depth, poll_s=args.poll)
    print(f"  recorder → {args.db}  actifs={','.join(assets)}  "
          f"poll={args.poll}s  profondeur={args.depth}")
    print("  Ctrl-C pour arrêter proprement\n")
    try:
        stats = mr.run(max_seconds=args.seconds, quiet=args.quiet)
    finally:
        rec.close()
    print("\n  arrêt propre")
    print(stats.line())
    print(f"  lignes écrites : {rec.written}")


if __name__ == "__main__":
    main()
