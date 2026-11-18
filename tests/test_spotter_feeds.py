"""Tests de SPOTTER, des indicateurs et des sources de prix.

Ces tests existent parce que leur absence a laissé passer un bug réel :
`ElderRay.snapshot` est une property, et SPOTTER l'appelait comme une méthode.
Le pipeline plantait au premier tick, ce qu'aucun test du noyau ne voyait.
"""
from __future__ import annotations

import asyncio

import pytest

from grokdesk.core.bus import Bus
from grokdesk.core.types import Snapshot, Tick, Window
from grokdesk.agents.spotter import Spotter
from grokdesk.feeds.base import PricePath, windows_over
from grokdesk.model.indicators import (BookImbalance, ElderRay, Ema,
                                       MicropriceMomentum)


# -------------------------------------------------------------- indicateurs
def test_ema_converge_vers_une_constante():
    e = Ema(13)
    for _ in range(300):
        e.update(100.0)
    assert e.value == pytest.approx(100.0)
    assert e.ready


def test_elder_ray_snapshot_est_une_property():
    """Le bug exact qui a cassé le pipeline : garde anti-régression."""
    er = ElderRay(13)
    er.update(101.0, 99.0, 100.0)
    snap = er.snapshot
    assert isinstance(snap, dict), "snapshot est une property, pas une méthode"
    assert not callable(snap)
    assert {"ema13", "bull_power", "bear_power", "net_power",
            "elder_ready"} <= set(snap)


def test_elder_ray_signes():
    er = ElderRay(13)
    for _ in range(30):
        er.update(100.5, 99.5, 100.0)      # EMA se cale sur 100
    assert er.bull > 0 and er.bear < 0
    assert er.net == pytest.approx(0.0, abs=1e-6)


def test_book_imbalance_bornee():
    b = BookImbalance()
    assert b.update([(1.0, 100.0)], [(1.1, 0.0)]) == pytest.approx(1.0)
    assert b.update([(1.0, 0.0)], [(1.1, 100.0)]) == pytest.approx(-1.0)
    assert b.update([(1.0, 50.0)], [(1.1, 50.0)]) == pytest.approx(0.0)
    assert b.update([], []) == 0.0


def test_momentum_signe():
    m = MicropriceMomentum(lookback=5)
    for p in (100.0, 101.0, 102.0):
        m.update(p)
    assert m.value > 0


# ------------------------------------------------------------------ spotter
def test_spotter_pas_pret_avant_assez_de_ticks():
    bus = Bus()
    sp = Spotter(bus)
    sp.ingest(Tick("paper", "BTC", 100.0, 1.0))
    w = Window("BTC", 100.0, 0.0, 300.0)
    assert asyncio.run(sp.publish(w, 2.0)) is None


def test_spotter_publie_un_snapshot_complet():
    bus = Bus()
    bus.declare("tape", Snapshot)
    recu = []

    async def on_tape(s):
        recu.append(s)

    bus.on("tape", on_tape)
    sp = Spotter(bus)
    path = PricePath.gbm(seconds=200, seed=3)
    for t in path.ticks():
        sp.ingest(t)
    w = Window("BTC", path.at(0.0), 0.0, 300.0)
    snap = asyncio.run(sp.publish(w, 201.0))

    assert snap is not None
    assert len(recu) == 1
    assert snap.vol_per_s > 0
    assert "net_power" in snap.indicators
    assert "vol_ratio" in snap.indicators


def test_spotter_detecte_un_flux_perime():
    bus = Bus()
    sp = Spotter(bus)
    for t in PricePath.gbm(seconds=100, seed=5).ticks():
        sp.ingest(t)
    w = Window("BTC", 100.0, 0.0, 300.0)
    frais = asyncio.run(sp.publish(w, 100.5))
    perime = asyncio.run(sp.publish(w, 130.0))
    assert not frais.stale
    assert perime.stale, "30 s de retard doit être signalé comme périmé"


# -------------------------------------------------------------- price path
def test_price_path_est_constant_par_morceaux():
    p = PricePath("BTC", [0.0, 10.0, 20.0], [100.0, 110.0, 120.0])
    assert p.at(-5.0) == 100.0
    assert p.at(0.0) == 100.0
    assert p.at(9.9) == 100.0, "pas d'interpolation vers le futur"
    assert p.at(10.0) == 110.0
    assert p.at(999.0) == 120.0


def test_price_path_resolution_lit_le_chemin():
    from grokdesk.core.types import Side
    p = PricePath("BTC", [0.0, 300.0], [100.0, 120.0])
    w = Window("BTC", 100.0, 0.0, 300.0)
    r = p.resolve(w)
    assert r.settle_price == 120.0
    assert r.outcome is Side.UP


def test_gbm_reproductible_et_de_vol_correcte():
    a = PricePath.gbm(seconds=500, seed=11)
    b = PricePath.gbm(seconds=500, seed=11)
    assert a._p == b._p, "même graine → même chemin"
    c = PricePath.gbm(seconds=500, seed=12)
    assert a._p != c._p


def test_windows_over_decoupe_sans_trou():
    p = PricePath.gbm(seconds=1200, seed=2)
    ws = windows_over(p, duration_s=300.0)
    assert len(ws) == 4
    for w in ws:
        assert w.strike == p.at(w.opened_ts), "strike = prix à l'ouverture"
        assert w.duration_s == 300.0
    assert ws[1].opened_ts == ws[0].expiry_ts, "fenêtres contiguës"


def test_price_path_rejette_une_entree_vide():
    with pytest.raises(ValueError):
        PricePath("BTC", [], [])
