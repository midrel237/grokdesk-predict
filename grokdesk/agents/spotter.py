"""SPOTTER — ingestion des ticks, vol, indicateurs, et détection de péremption.

Hérite de l'idée la plus utile de RADAR : dans la version pump.fun, un agent
pollait trop lentement et signalait des coins déjà gradués (`stats["stale"]`).
C'était un gadget de démo ; ici c'est un mécanisme de sécurité réel. Un prix
spot vieux de 5 secondes sur une fenêtre de 5 minutes n'est pas une
information dégradée, c'est une information fausse, et EDGE doit s'abstenir.
"""
from __future__ import annotations

from ..core.bus import Bus
from ..core.config import PriorConfig
from ..core.types import Snapshot, Tick, Window
from ..model.indicators import BookImbalance, ElderRay, MicropriceMomentum
from ..model.vol import EwmaVol, RealizedVol


class Spotter:
    def __init__(self, bus: Bus, cfg: PriorConfig | None = None,
                 bar_seconds: float = 10.0) -> None:
        self.bus = bus
        cfg = cfg or PriorConfig()
        self.vol = EwmaVol(halflife_s=cfg.vol_halflife_s,
                           floor=cfg.vol_floor_per_s)
        self.vol_slow = RealizedVol()
        self.elder = ElderRay(13)
        self.imbalance = BookImbalance()
        self.momentum = MicropriceMomentum()
        self.bar_seconds = bar_seconds
        self._bar: dict | None = None
        self.last_tick: Tick | None = None
        self.ticks = 0

    def _roll_bar(self, tick: Tick) -> None:
        """Agrège les ticks en barres avant de nourrir Elder Ray, qui est un
        indicateur de barres et non de ticks."""
        px = tick.microprice
        if self._bar is None:
            self._bar = {"open_ts": tick.ts, "h": px, "l": px, "c": px}
            return
        b = self._bar
        b["h"] = max(b["h"], px)
        b["l"] = min(b["l"], px)
        b["c"] = px
        if tick.ts - b["open_ts"] >= self.bar_seconds:
            self.elder.update(b["h"], b["l"], b["c"])
            self._bar = {"open_ts": tick.ts, "h": px, "l": px, "c": px}

    def ingest(self, tick: Tick, book: tuple | None = None) -> None:
        self.ticks += 1
        self.vol.update(tick.microprice, tick.ts)
        self.vol_slow.update(tick.microprice, tick.ts)
        self.momentum.update(tick.microprice)
        self._roll_bar(tick)
        if book is not None:
            bids, asks = book
            self.imbalance.update(bids, asks)
        self.last_tick = tick

    def indicators(self) -> dict[str, float]:
        out = dict(self.elder.snapshot)   # `snapshot` est une property
        out["book_imbalance"] = self.imbalance.value
        out["momentum"] = self.momentum.value
        fast, slow = self.vol.value, self.vol_slow.value
        out["vol_fast_per_s"] = fast
        out["vol_slow_per_s"] = slow
        out["vol_ratio"] = (fast / slow) if slow > 0 else 1.0
        return out

    async def publish(self, window: Window, now: float) -> Snapshot | None:
        """Publie un Snapshot sur le topic 'tape'. None si pas encore prêt."""
        if self.last_tick is None or not self.vol.ready:
            return None
        snap = Snapshot(
            window=window,
            tick=self.last_tick,
            now=now,
            vol_per_s=self.vol.value,
            feed_age_s={self.last_tick.venue: max(0.0, now - self.last_tick.ts)},
            indicators=self.indicators(),
        )
        await self.bus.emit("tape", snap)
        return snap
