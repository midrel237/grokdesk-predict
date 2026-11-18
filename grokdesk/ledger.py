from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Ledger:
    """Wallet plus cold vault. TREASURY is the only desk allowed to move
    money between them, and it freezes the desk if the wallet ever drops
    below where it started."""

    start_sol: float
    wallet_sol: float = 0.0
    vault_sol: float = 0.0
    gas_paid: float = 0.0
    trades: list = field(default_factory=list)

    def __post_init__(self) -> None:
        self.wallet_sol = self.start_sol

    @property
    def net_sol(self) -> float:
        return round(self.wallet_sol + self.vault_sol, 4)

    @property
    def wins(self) -> int:
        return sum(1 for t in self.trades if t.pnl_sol > 0)

    @property
    def losses(self) -> int:
        return sum(1 for t in self.trades if t.pnl_sol <= 0)

    @property
    def loss_cost(self) -> float:
        return round(-sum(t.pnl_sol for t in self.trades if t.pnl_sol <= 0), 4)

    def sweep(self, keep: float) -> float:
        """Move half of anything above `keep` into the cold vault."""
        excess = max(0.0, self.wallet_sol - keep)
        move = round(excess * 0.5, 4)
        self.wallet_sol -= move
        self.vault_sol += move
        return move
