"""Adaptateur Pocket Option — **lecture seule**, et délibérément.

Il n'existe aucune API officielle. Les bibliothèques disponibles
(`pocketoptionapi-async`, `BinaryOptionsToolsV2`, ...) fonctionnent en
extrayant à la main le cookie SSID depuis le navigateur puis en parlant le
WebSocket interne de la plateforme. Conséquences assumées ici :

- `place_order` n'existe pas dans ce module. L'exécution n'est pas exposée.
- Le rôle de cet adaptateur est de **relever les payouts réels par actif** et
  d'échantillonner leur flux de prix, parce que ce sont les deux inconnues qui
  déterminent si un edge est atteignable : le payout fixe le seuil
  d'équilibre (`1/(1+payout)`), et leurs cotations sont synthétiques, donc
  potentiellement divergentes du marché de référence.
- `divergence()` mesure précisément cet écart. Un flux broker qui dérive du
  spot réel est une raison de ne pas trader là, et c'est mesurable.
"""
from __future__ import annotations

import statistics
import time
from dataclasses import dataclass, field

from ..core.types import Tick


@dataclass
class PayoutObservation:
    asset: str
    payout: float
    ts: float

    @property
    def break_even(self) -> float:
        return 1.0 / (1.0 + self.payout)


@dataclass
class PayoutLog:
    """Historique des payouts observés, par actif.

    Le payout est fixé par la contrepartie et varie par actif (les relevés
    publics vont de 45 % à 96 % selon la source). On ne le suppose donc
    jamais : on le relève, et on garde la trace de ses variations.
    """
    obs: dict[str, list[PayoutObservation]] = field(default_factory=dict)

    def record(self, asset: str, payout: float, ts: float | None = None) -> None:
        self.obs.setdefault(asset, []).append(
            PayoutObservation(asset, payout, ts or time.time()))

    def latest(self, asset: str) -> float | None:
        lst = self.obs.get(asset)
        return lst[-1].payout if lst else None

    def summary(self, asset: str) -> dict | None:
        lst = self.obs.get(asset)
        if not lst:
            return None
        vals = [o.payout for o in lst]
        return {
            "asset": asset,
            "n": len(vals),
            "min": min(vals),
            "max": max(vals),
            "median": statistics.median(vals),
            "break_even_at_median": 1.0 / (1.0 + statistics.median(vals)),
            "break_even_worst": 1.0 / (1.0 + min(vals)),
        }


class PocketOptionReadOnly:
    """Connexion en lecture seule. Nécessite un SSID extrait du navigateur.

    Aucune méthode d'exécution n'est fournie, par choix : automatiser via un
    socket non documenté et une session recopiée du navigateur est fragile et
    entre très probablement en conflit avec les CGU du broker.
    """

    def __init__(self, ssid: str | None = None) -> None:
        self.ssid = ssid
        self.payouts = PayoutLog()
        self._client = None

    @staticmethod
    def available() -> bool:
        try:
            import pocketoptionapi_async  # noqa: F401
            return True
        except ImportError:
            return False

    async def connect(self):  # pragma: no cover - requiert une vraie session
        if not self.ssid:
            raise RuntimeError(
                "SSID requis : se connecter dans Chrome, DevTools → "
                "Application → Cookies → valeur de 'SSID'."
            )
        try:
            from pocketoptionapi_async import AsyncPocketOptionClient
        except ImportError as e:
            raise RuntimeError(
                "pocketoptionapi-async absent. Cette lib n'est pas officielle "
                "et peut casser à tout changement côté serveur."
            ) from e
        self._client = AsyncPocketOptionClient(self.ssid)
        await self._client.connect()
        return self._client

    async def sample_payouts(self, assets: tuple[str, ...]) -> dict[str, float]:  # pragma: no cover
        """Relève le payout courant de chaque actif et l'archive."""
        if self._client is None:
            await self.connect()
        out: dict[str, float] = {}
        for a in assets:
            raw = await self._client.get_payout(a)
            payout = float(raw) / 100.0 if float(raw) > 1.5 else float(raw)
            self.payouts.record(a, payout)
            out[a] = payout
        return out

    @staticmethod
    def divergence(broker_ticks: list[Tick], reference_ticks: list[Tick]) -> dict:
        """Écart relatif entre le flux du broker et un flux de référence.

        Une divergence persistante signifie qu'on ne trade pas le marché qu'on
        modélise — auquel cas aucune calibration faite sur le spot réel n'est
        transférable aux cotations du broker.
        """
        if not broker_ticks or not reference_ticks:
            return {"n": 0}
        ref = sorted(reference_ticks, key=lambda t: t.ts)
        ref_t = [t.ts for t in ref]
        import bisect
        diffs = []
        for bt in broker_ticks:
            i = min(len(ref) - 1, max(0, bisect.bisect_right(ref_t, bt.ts) - 1))
            rp = ref[i].price
            if rp > 0:
                diffs.append((bt.price - rp) / rp)
        if not diffs:
            return {"n": 0}
        return {
            "n": len(diffs),
            "mean_bps": statistics.fmean(diffs) * 1e4,
            "stdev_bps": (statistics.stdev(diffs) * 1e4 if len(diffs) > 1 else 0.0),
            "max_abs_bps": max(abs(d) for d in diffs) * 1e4,
        }
