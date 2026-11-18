"""Adaptateur **Cryptofeed** — flux spot temps réel normalisé.

Pourquoi Cryptofeed plutôt qu'un WebSocket écrit à la main : il gère la partie
pénible et non différenciante — reconnexion, séquencement, normalisation des
symboles entre exchanges, backfill après coupure. C'est exactement le rôle
prévu pour `feeds/spot.py` dans le plan.

Import paresseux : ce module ne s'importe qu'au moment du `start()`.
"""
from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Callable

from ..core.types import Tick


class CryptofeedSpot:
    """Enveloppe Cryptofeed et republie des `Tick` sur un callback.

    `exchange` est un nom Cryptofeed ('BINANCE', 'COINBASE', ...), `symbols`
    des paires normalisées ('BTC-USDT').
    """

    def __init__(self, exchange: str = "BINANCE",
                 symbols: tuple[str, ...] = ("BTC-USDT",),
                 on_tick: Callable[[Tick], None] | None = None,
                 maxlen: int = 50_000) -> None:
        self.exchange = exchange
        self.symbols = symbols
        self.on_tick = on_tick
        self.buffer: deque[Tick] = deque(maxlen=maxlen)
        self._fh = None

    @staticmethod
    def available() -> bool:
        try:
            import cryptofeed  # noqa: F401
            return True
        except ImportError:
            return False

    def _emit(self, tick: Tick) -> None:
        self.buffer.append(tick)
        if self.on_tick:
            self.on_tick(tick)

    def start(self) -> None:
        """Démarre la boucle Cryptofeed. Bloquant — à lancer dans son thread
        ou son process ; le recorder en est le consommateur naturel."""
        try:
            from cryptofeed import FeedHandler
            from cryptofeed.defines import L2_BOOK, TRADES
            from cryptofeed.exchanges import EXCHANGE_MAP
        except ImportError as e:  # pragma: no cover - dépend de l'install
            raise RuntimeError(
                "cryptofeed absent. `pip install cryptofeed`, ou utiliser "
                "feeds.base.PricePath pour travailler hors ligne."
            ) from e

        async def trade_cb(trade, receipt_ts):
            self._emit(Tick(venue=self.exchange,
                            asset=str(trade.symbol).split("-")[0],
                            price=float(trade.price),
                            ts=float(receipt_ts or time.time())))

        async def book_cb(book, receipt_ts):
            bids, asks = book.book.bids, book.book.asks
            if not bids or not asks:
                return
            bid = float(bids.index(0)[0])
            ask = float(asks.index(0)[0])
            self._emit(Tick(venue=self.exchange,
                            asset=str(book.symbol).split("-")[0],
                            price=(bid + ask) / 2.0,
                            ts=float(receipt_ts or time.time()),
                            bid=bid, ask=ask))

        fh = FeedHandler()
        cls = EXCHANGE_MAP[self.exchange]
        fh.add_feed(cls(symbols=list(self.symbols),
                        channels=[TRADES, L2_BOOK],
                        callbacks={TRADES: trade_cb, L2_BOOK: book_cb}))
        self._fh = fh
        fh.run()

    async def start_async(self) -> None:
        """Lance la boucle bloquante dans un thread pour cohabiter avec le
        pipeline asyncio du desk."""
        await asyncio.to_thread(self.start)
