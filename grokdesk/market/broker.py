from __future__ import annotations

from time import time

from ..types import Position, Trade


class PaperBroker:
    """Executes buys and ladder exits against a coin's bonding curve.

    Paper only. It moves numbers in the Ledger and never talks to a chain.
    """

    GAS = 0.003

    def __init__(self, ledger) -> None:
        self.ledger = ledger
        self.open: dict[str, Position] = {}

    def buy(self, coin, size_sol: float, agent: str) -> Position:
        coin.curve.buy(size_sol)
        pos = Position(coin.mint, coin.symbol, size_sol, coin.curve.price, agent)
        self.open[coin.mint] = pos
        self.ledger.wallet_sol -= size_sol + self.GAS
        self.ledger.gas_paid += self.GAS
        return pos

    def settle(self, coin, pos: Position, realized_mult: float) -> Trade:
        """Close a position at a realised multiple. ROUTER ladders out in
        tranches, so `realized_mult` is the blended exit, not a single tick."""
        proceeds = pos.size_sol * realized_mult
        pnl = round(proceeds - pos.size_sol, 4)
        self.ledger.wallet_sol += proceeds - self.GAS
        self.ledger.gas_paid += self.GAS
        trade = Trade(coin.mint, coin.symbol, pos.agent, pos.size_sol,
                      pos.entry, pos.entry * realized_mult, pnl,
                      time() - pos.ts, coin.graduated)
        self.ledger.trades.append(trade)
        self.open.pop(coin.mint, None)
        return trade
