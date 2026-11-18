"""Tests du noyau : bus, config, ledger, matrice de scoring.

Le dépôt n'avait aucun test. Ces modules sont ceux repris de la version
pump.fun, donc ceux à verrouiller en premier.
"""
from __future__ import annotations

import asyncio
from datetime import date

import pytest

from grokdesk.core.bus import Bus
from grokdesk.core.config import load
from grokdesk.core.ledger import Ledger
from grokdesk.core.scoring import ScoringMatrix
from grokdesk.core.types import (Closed, Order, Resolution, Side, Signal,
                                 Window)


# --------------------------------------------------------------------- bus
def test_bus_isole_les_exceptions():
    """Correction du défaut d'origine : un handler qui lève ne doit pas
    empêcher les autres de tourner."""
    bus = Bus()
    vus = []

    async def casse(_):
        raise RuntimeError("boom")

    async def marche(p):
        vus.append(p)

    bus.on("t", casse)
    bus.on("t", marche)
    asyncio.run(bus.emit("t", 42))

    assert vus == [42], "le second handler doit avoir tourné malgré le premier"
    assert len(bus.errors) == 1
    assert bus.errors[0][0] == "t"


def test_bus_contrat_de_type():
    bus = Bus()
    bus.declare("sig", Signal)
    with pytest.raises(TypeError, match="attend Signal"):
        asyncio.run(bus.emit("sig", {"pas": "un signal"}))


# ------------------------------------------------------------------ config
def test_config_est_reellement_chargee():
    """L'ancien desk.yaml n'était jamais lu. Vérifie que ça a changé."""
    cfg = load()
    assert cfg.dry_run is True
    assert cfg.bankroll == 1000.0
    assert cfg.risk.kelly_fraction == 0.25
    assert cfg.risk.max_stake_fraction == 0.02
    assert cfg.edge.min_payout == 0.70
    assert "BTC" in cfg.assets


def test_live_exige_deux_confirmations(monkeypatch, tmp_path):
    """dry_run: false seul ne doit pas suffire à passer en live."""
    p = tmp_path / "d.yaml"
    p.write_text("dry_run: false\nbankroll: 10.0\n", encoding="utf-8")
    monkeypatch.delenv("GROKDESK_DRY_RUN", raising=False)
    cfg = load(p)
    assert cfg.dry_run is True, "sans confirmation d'environnement → papier"


# ------------------------------------------------------------------ ledger
def _closed(stake: float, won: bool, payout: float = 0.92, ts: float = 0.0):
    w = Window("BTC", 100.0, 0.0, 300.0)
    o = Order(w, Side.UP, stake, None, ts, order_id="X")
    o.filled = True
    o.fill_ts = ts
    r = Resolution(w, 101.0 if won else 99.0, 300.0 + ts)
    return Closed(o, r, won, stake * payout if won else -stake, 0.0,
                  0.6, 300.0)


def test_ledger_conserve_la_coherence():
    led = Ledger(1000.0)
    d = date(2026, 1, 1)
    led.commit(20.0, 0.0, d)
    assert led.cash == 980.0
    assert led.exposure == 20.0
    assert led.equity == 1000.0, "engager une mise ne change pas l'equity"

    led.settle(_closed(20.0, True), d)
    assert led.exposure == 0.0
    assert led.cash == pytest.approx(1018.4)
    assert led.wins == 1 and led.losses == 0


def test_ledger_refuse_de_miser_plus_que_le_cash():
    led = Ledger(10.0)
    with pytest.raises(ValueError, match="supérieure au cash"):
        led.commit(20.0, 0.0, date(2026, 1, 1))


def test_journees_rouges_consecutives():
    led = Ledger(1000.0)
    for i, won in enumerate([False, False, False]):
        d = date(2026, 1, 1 + i)
        led.commit(10.0, 0.0, d)
        led.settle(_closed(10.0, won, ts=i * 86400), d)
    assert led.consecutive_red_days() == 3


# ----------------------------------------------------------------- scoring
def test_matrice_bornee():
    """La boucle d'auto-tuning ne doit pas pouvoir étrangler le desk."""
    m = ScoringMatrix()
    for _ in range(200):
        m.tighten("spam", min_edge=+0.01)
    lo, hi = ScoringMatrix._CLAMP["min_edge"]
    assert m.min_edge == hi, "doit saturer à la borne haute"

    for _ in range(500):
        m.tighten("spam", min_edge=-0.01)
    assert m.min_edge == lo, "doit saturer à la borne basse"


def test_matrice_refuse_un_seuil_inconnu():
    with pytest.raises(KeyError):
        ScoringMatrix().tighten("x", seuil_imaginaire=0.1)


def test_matrice_persiste(tmp_path):
    m = ScoringMatrix()
    m.tighten("note", min_edge=+0.01)
    f = tmp_path / "m.json"
    m.save(f)
    assert ScoringMatrix.load(f).min_edge == m.min_edge
