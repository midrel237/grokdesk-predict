from __future__ import annotations


class BondingCurve:
    """A pump.fun style constant-product bonding curve (paper).

    Price is virtual_sol / virtual_tokens. Buying pushes SOL into the curve
    and price up; graduation happens once real SOL crossing the curve passes
    GRAD_SOL, at which point the token would migrate to a DEX.
    """

    GRAD_SOL = 85.0

    def __init__(self, virtual_sol: float = 30.0, virtual_tokens: float = 1.073e9) -> None:
        self.vsol = virtual_sol
        self.vtok = virtual_tokens
        self.real_sol = 0.0

    @property
    def price(self) -> float:
        return self.vsol / self.vtok

    def buy(self, sol: float) -> float:
        k = self.vsol * self.vtok
        self.vsol += sol
        self.real_sol += sol
        new_tok = k / self.vsol
        got = self.vtok - new_tok
        self.vtok = new_tok
        return got

    def sell(self, tokens: float) -> float:
        k = self.vsol * self.vtok
        self.vtok += tokens
        new_sol = k / self.vtok
        out = self.vsol - new_sol
        self.vsol = new_sol
        self.real_sol -= out
        return out

    @property
    def graduated(self) -> bool:
        return self.real_sol >= self.GRAD_SOL
