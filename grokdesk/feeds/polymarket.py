"""Adaptateur Polymarket — Gamma API + CLOB, en accès direct.

Correction d'une erreur du plan : j'avais écrit que `ccxt.prediction.polymarket`
existait, en me fiant à de la documentation. Vérifié contre la lib installée,
**c'est faux** : ccxt 4.5.64 expose 105 exchanges et aucun marché de
prédiction. L'adaptateur ccxt a donc été supprimé.

Ce qui fonctionne, vérifié en direct le 18/11/2026 :

* Gamma `GET /markets?slug=...` — sans authentification. Le slug est
  **déterministe** : `{asset}-updown-5m-{borne_unix_5min}`, exactement le motif
  que `trade.md` prévoyait. Aucune recherche n'est nécessaire : on le
  construit. Les fenêtres courante et les deux suivantes existent déjà.
* CLOB `GET /book?token_id=...` — sans authentification, 50 niveaux par côté.

Conséquence appréciable : tout le chemin **lecture seule** tient en stdlib,
sans aucune dépendance. L'écriture (placement d'ordres) passera par
`py-clob-client` officiel, et reste hors de ce module.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
# Client générique volontairement : aucune donnée personnelle dans l'UA.
_UA = {"User-Agent": "grokdesk/0.1"}

WINDOW_S = 300


def window_slug(asset: str, start_unix: int, minutes: int = 5) -> str:
    """`btc-updown-5m-1795003200`. Le slug encode la borne de début."""
    return f"{asset.lower()}-updown-{minutes}m-{int(start_unix)}"


def current_window_start(now: float | None = None, window_s: int = WINDOW_S) -> int:
    n = int(now if now is not None else time.time())
    return n - (n % window_s)


@dataclass
class Book:
    token_id: str
    bids: list[tuple[float, float]]
    asks: list[tuple[float, float]]
    ts: float

    @property
    def best_bid(self) -> float | None:
        return max((p for p, _ in self.bids), default=None)

    @property
    def best_ask(self) -> float | None:
        return min((p for p, _ in self.asks), default=None)

    @property
    def mid(self) -> float | None:
        b, a = self.best_bid, self.best_ask
        return (b + a) / 2.0 if (b is not None and a is not None) else None

    @property
    def spread(self) -> float | None:
        b, a = self.best_bid, self.best_ask
        return (a - b) if (b is not None and a is not None) else None

    @property
    def implied_payout(self) -> float | None:
        """Payout implicite d'un achat au meilleur ask.

        Acheter un contrat à `ask` rapporte `1 - ask` pour une mise de `ask`,
        soit un payout de `(1 - ask) / ask`. C'est ce qui rend un marché
        d'événement directement comparable à un broker binaire à payout fixe,
        et la comparaison est l'argument central :

            Polymarket, ask 0.51  → payout 96.1 %, seuil d'équilibre 51.00 %
            Broker binaire 92 %   → payout 92.0 %, seuil d'équilibre 52.08 %

        Un point de seuil en moins, c'est tout l'edge quand l'edge vaut 1,5 ¢.
        """
        a = self.best_ask
        if a is None or not (0.0 < a < 1.0):
            return None
        return (1.0 - a) / a

    @property
    def break_even(self) -> float | None:
        """Pour un achat au meilleur ask, le seuil EST le prix payé."""
        a = self.best_ask
        return a if (a is not None and 0.0 < a < 1.0) else None

    def depth(self, levels: int = 5) -> tuple[float, float]:
        return (sum(q for _, q in self.bids[:levels]),
                sum(q for _, q in self.asks[:levels]))


@dataclass
class UpDownMarket:
    slug: str
    question: str
    asset: str
    start_unix: int
    end_iso: str
    token_up: str
    token_down: str
    closed: bool

    @property
    def expiry_unix(self) -> int:
        return self.start_unix + WINDOW_S


class Polymarket:
    """Lecture seule. Aucune clé, aucune signature, aucune écriture possible."""

    def __init__(self, timeout: float = 15.0) -> None:
        self.timeout = timeout
        self.calls = 0
        self.errors = 0

    def _get(self, url: str):
        self.calls += 1
        try:
            req = urllib.request.Request(url, headers=_UA)
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read())
        except (urllib.error.URLError, json.JSONDecodeError, TimeoutError):
            self.errors += 1
            raise

    # --- découverte ------------------------------------------------------
    def market(self, asset: str, start_unix: int) -> UpDownMarket | None:
        slug = window_slug(asset, start_unix)
        rows = self._get(f"{GAMMA}/markets?slug={urllib.parse.quote(slug)}")
        if not rows:
            return None
        m = rows[0]
        toks = m.get("clobTokenIds")
        toks = json.loads(toks) if isinstance(toks, str) else (toks or [])
        if len(toks) < 2:
            return None
        return UpDownMarket(
            slug=slug,
            question=m.get("question", ""),
            asset=asset.upper(),
            start_unix=int(start_unix),
            end_iso=m.get("endDate", ""),
            token_up=toks[0],
            token_down=toks[1],
            closed=bool(m.get("closed")),
        )

    def upcoming(self, asset: str, ahead: int = 2,
                 now: float | None = None) -> list[UpDownMarket]:
        """Fenêtre courante + les `ahead` suivantes. Les absentes sont ignorées."""
        base = current_window_start(now)
        out = []
        for i in range(ahead + 1):
            try:
                m = self.market(asset, base + i * WINDOW_S)
            except Exception:
                continue
            if m is not None and not m.closed:
                out.append(m)
        return out

    # --- carnet ----------------------------------------------------------
    def book(self, token_id: str) -> Book:
        """Carnet normalisé : `bids[0]` et `asks[0]` sont TOUJOURS le meilleur.

        Le CLOB renvoie les niveaux du pire au meilleur (bids croissants, asks
        décroissants). Les archiver positionnellement sans retrier mettait le
        niveau le plus éloigné en `level=0` — un spread apparent de 0.98 sur un
        marché coté 0.50/0.51. `best_bid`/`best_ask` restaient justes car ils
        prennent max/min, mais la profondeur archivée était inexploitable.
        On trie donc à la source, une fois.
        """
        raw = self._get(f"{CLOB}/book?token_id={urllib.parse.quote(token_id)}")

        def side(key, best_first_desc: bool):
            rows = [(float(x["price"]), float(x["size"]))
                    for x in (raw.get(key) or [])
                    if x.get("price") is not None and x.get("size") is not None]
            return sorted(rows, key=lambda r: r[0], reverse=best_first_desc)

        return Book(token_id=token_id,
                    bids=side("bids", True),    # meilleur bid = le plus haut
                    asks=side("asks", False),   # meilleur ask = le plus bas
                    ts=time.time())

    def quote(self, m: UpDownMarket) -> dict:
        """Les deux côtés d'une fenêtre, avec payouts implicites."""
        up, down = self.book(m.token_up), self.book(m.token_down)
        return {
            "slug": m.slug,
            "asset": m.asset,
            "start_unix": m.start_unix,
            "expiry_unix": m.expiry_unix,
            "up": up,
            "down": down,
            "best_payout": max([p for p in (up.implied_payout,
                                            down.implied_payout)
                                if p is not None], default=None),
            "ts": time.time(),
        }
