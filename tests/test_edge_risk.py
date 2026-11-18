"""Tests d'EDGE, KELLY, du risk engine et du moteur papier.

Contient le test qui porte l'argument central du projet :
`test_edge_impossible_quand_strike_egale_entree`.
"""
from __future__ import annotations

from datetime import date

import pytest

from grokdesk.agents.edge import Edge, break_even
from grokdesk.agents.kelly import Kelly, kelly_full
from grokdesk.core.config import EdgeConfig, RiskConfig
from grokdesk.core.ledger import Ledger
from grokdesk.core.types import (Fair, Side, Signal, Snapshot, Tick, Window)
from grokdesk.execution.paper import PaperEngine, PaperVenue
from grokdesk.model.prior import digital_p_up
from grokdesk.risk.engine import RiskEngine


def _snap(window, now=0.0, vol=4e-4, age=0.1, ind=None):
    return Snapshot(window=window,
                    tick=Tick("paper", "BTC", window.strike, now - age),
                    now=now, vol_per_s=vol,
                    feed_age_s={"paper": age}, indicators=ind or {})


# -------------------------------------------------------------- break-even
def test_seuil_equilibre():
    """Le tableau qui décide de la viabilité d'un broker binaire."""
    assert break_even(0.92) == pytest.approx(0.520833, abs=1e-6)
    assert break_even(0.85) == pytest.approx(0.540540, abs=1e-6)
    assert break_even(0.80) == pytest.approx(0.555556, abs=1e-6)
    assert break_even(0.50) == pytest.approx(0.666667, abs=1e-6)
    assert break_even(1.00) == 0.5, "payout 100 % = jeu équitable"


# ------------------------------------------------- L'ARGUMENT CENTRAL
@pytest.mark.parametrize("payout", [0.60, 0.70, 0.80, 0.85, 0.92, 0.96])
def test_edge_impossible_quand_strike_egale_entree(payout):
    """Sur un binaire où le strike EST le prix d'entrée, l'edge est négatif
    pour tout payout < 100 %, quel que soit le modèle.

    Démonstration : log(spot/strike) = 0 → p = 0.5 → edge = 0.5 − 1/(1+payout),
    qui est < 0 dès que payout < 1. C'est structurel, pas un défaut de
    calibration : aucune amélioration du modèle ne peut le corriger. Seule une
    prévision directionnelle (dérive non nulle) peut produire un edge.
    """
    spot = 100_000.0
    p = digital_p_up(spot=spot, strike=spot, vol_per_s=4e-4, horizon_s=300.0)
    edge = max(p, 1 - p) - break_even(payout)
    assert edge < 0, f"payout {payout}: edge devrait être négatif, vaut {edge}"


def test_edge_sabstient_en_mode_entree():
    """Conséquence opérationnelle : EDGE n'émet aucun signal."""
    e = Edge(EdgeConfig())
    spot = 100_000.0
    w = Window("BTC", spot, 0.0, 300.0)
    for i in range(50):
        now = 10.0 + i
        fair = Fair(window=w, p_up=digital_p_up(spot, spot, 4e-4, 300.0 - i),
                    vol_per_s=4e-4, horizon_s=300.0 - i,
                    log_moneyness=0.0, now=now)
        assert e.evaluate(fair, _snap(w, now), 0.92) is None
    assert e.emitted == 0
    assert e.abstain_reasons.get("edge_insuffisant") == 50


def test_edge_apparait_quand_le_strike_est_fige():
    """En mode fenêtre (type marché d'événement), le prix peut s'éloigner du
    strike et l'edge devient réel."""
    e = Edge(EdgeConfig())
    w = Window("BTC", 100_000.0, 0.0, 300.0)
    spot = 100_400.0                     # le prix a monté depuis l'ouverture
    p = digital_p_up(spot, w.strike, 4e-4, 60.0)
    fair = Fair(w, p, 4e-4, 60.0, 0.004, now=240.0)
    sig = e.evaluate(fair, _snap(w, 240.0), 0.92)
    assert sig is not None
    assert sig.side is Side.UP
    assert sig.edge > 0.02


# ------------------------------------------------------------- abstentions
def test_abstention_sur_flux_perime():
    e = Edge(EdgeConfig())
    w = Window("BTC", 100_000.0, 0.0, 300.0)
    fair = Fair(w, 0.95, 4e-4, 60.0, 0.01, now=240.0)
    assert e.evaluate(fair, _snap(w, 240.0, age=5.0), 0.92) is None
    assert e.abstain_reasons.get("flux_perime") == 1


def test_abstention_sur_payout_trop_bas():
    e = Edge(EdgeConfig())
    w = Window("BTC", 100_000.0, 0.0, 300.0)
    fair = Fair(w, 0.99, 4e-4, 60.0, 0.02, now=240.0)
    assert e.evaluate(fair, _snap(w, 240.0), 0.50) is None
    assert e.abstain_reasons.get("payout_trop_bas") == 1


def test_abstention_horizon_trop_court():
    e = Edge(EdgeConfig())
    w = Window("BTC", 100_000.0, 0.0, 300.0)
    fair = Fair(w, 0.99, 4e-4, 5.0, 0.02, now=295.0)
    assert e.evaluate(fair, _snap(w, 295.0), 0.92) is None
    assert e.abstain_reasons.get("horizon_trop_court") == 1


# ------------------------------------------------------------------- kelly
def test_kelly_formule():
    assert kelly_full(1.0, 0.92) == pytest.approx(1.0)
    assert kelly_full(break_even(0.92), 0.92) == pytest.approx(0.0, abs=1e-12)
    assert kelly_full(0.4, 0.92) < 0


def test_kelly_jamais_au_dela_du_cap():
    k = Kelly(RiskConfig(max_stake_fraction=0.02, kelly_fraction=0.25))
    sig = Signal(Window("BTC", 1.0, 0.0, 300.0), Side.UP, p=0.95,
                 payout=0.92, break_even=break_even(0.92), edge=0.43,
                 stake_fraction=0.0, confidence="forte", reasons=(), now=0.0)
    assert k.size(sig, calibrated=True).stake_fraction <= 0.02


def test_kelly_divise_par_deux_si_non_calibre():
    k = Kelly(RiskConfig(max_stake_fraction=1.0, kelly_fraction=0.25))
    sig = Signal(Window("BTC", 1.0, 0.0, 300.0), Side.UP, p=0.60,
                 payout=0.92, break_even=break_even(0.92), edge=0.08,
                 stake_fraction=0.0, confidence="forte", reasons=(), now=0.0)
    assert (k.size(sig, calibrated=False).stake_fraction
            == pytest.approx(k.size(sig, calibrated=True).stake_fraction / 2))


def test_kelly_refuse_un_pari_defavorable():
    k = Kelly()
    sig = Signal(Window("BTC", 1.0, 0.0, 300.0), Side.UP, p=0.40,
                 payout=0.92, break_even=break_even(0.92), edge=-0.12,
                 stake_fraction=0.0, confidence="faible", reasons=(), now=0.0)
    assert k.size(sig).stake_fraction == 0.0


# -------------------------------------------------------------------- risk
def _sig(frac=0.01):
    return Signal(Window("BTC", 1.0, 0.0, 300.0), Side.UP, 0.6, 0.92,
                  break_even(0.92), 0.08, frac, "forte", (), 0.0)


def test_risk_bloque_au_dela_des_positions_ouvertes():
    r = RiskEngine(Ledger(1000.0), RiskConfig(max_open_positions=3))
    r.open_positions = 3
    b = r.allow(_sig(), date(2026, 1, 1))
    assert b is not None and b.reason == "trop_de_positions"


def test_risk_bloque_sur_quota_horaire():
    r = RiskEngine(Ledger(1000.0), RiskConfig(max_entries_per_hour=6))
    r.entries_this_hour = 6
    assert r.allow(_sig(), date(2026, 1, 1)).reason == "quota_horaire"


def test_risk_bloque_une_mise_hors_cap():
    r = RiskEngine(Ledger(1000.0), RiskConfig(max_stake_fraction=0.02))
    assert r.allow(_sig(0.5), date(2026, 1, 1)).reason == "mise_hors_cap"


def test_kill_switch_sur_drawdown_journalier():
    led = Ledger(1000.0)
    d = date(2026, 1, 1)
    from grokdesk.core.types import Closed, Order, Resolution
    for i in range(7):
        led.commit(10.0, 0.0, d)
        w = Window("BTC", 100.0, 0.0, 300.0)
        o = Order(w, Side.UP, 10.0, None, 0.0, order_id=f"X{i}")
        o.filled = True
        led.settle(Closed(o, Resolution(w, 99.0, 300.0), False, -10.0, 0.0,
                          0.6, 300.0), d)
    r = RiskEngine(led, RiskConfig(daily_drawdown_stop=0.06))
    r.check_kill_switch(d)
    assert r.frozen
    assert r.allow(_sig(), d).reason == "gele"


def test_aucun_agent_ne_peut_lever_un_gel():
    """Le risk engine garde la primauté revendiquée par la version pump.fun,
    et cette fois c'est vérifié."""
    r = RiskEngine(Ledger(1000.0))
    r.freeze("test")
    for usurpateur in ("EDGE", "TAKER", "GROK CORE", "head", "audit"):
        with pytest.raises(PermissionError):
            r.unfreeze(usurpateur)
    assert r.frozen
    r.unfreeze("operator")
    assert not r.frozen


def test_state_est_la_seule_source_de_verite():
    """Corrige le HUD menteur de l'original, qui affichait [FROZEN] sur un
    seuil différent du vrai gel."""
    r = RiskEngine(Ledger(1000.0))
    assert r.state()["frozen"] is False
    r.freeze("motif")
    st = r.state()
    assert st["frozen"] is True and st["freeze_reasons"] == ["motif"]


# ------------------------------------------------------------ moteur papier
def test_moteur_papier_applique_la_latence():
    eng = PaperEngine(PaperVenue(latency_s=0.5))
    o = eng.place(_sig(), 10.0, now=100.0)
    assert o.filled and o.fill_ts == 100.5


def test_moteur_papier_rejette_si_le_payout_glisse_sous_le_seuil():
    eng = PaperEngine(PaperVenue())
    o = eng.place(_sig(), 10.0, now=0.0, payout_at_fill=0.30)
    assert not o.filled
    assert o.rejected == "payout_sous_seuil_a_l_arrivee"
    assert eng.rejected == 1


def test_resolution_lue_et_non_tiree():
    """Le remplacement du tirage caché de l'ancien runtime.on_entry()."""
    from grokdesk.core.types import Resolution
    eng = PaperEngine(PaperVenue())
    w = Window("BTC", 100.0, 0.0, 300.0)
    sig = Signal(w, Side.UP, 0.6, 0.92, break_even(0.92), 0.08, 0.01,
                 "forte", (), 0.0)
    o = eng.place(sig, 10.0, now=0.0)

    gagnant = eng.resolve(o, Resolution(w, 101.0, 300.0), 0.92)
    assert gagnant.won and gagnant.pnl == pytest.approx(9.2)

    o2 = eng.place(sig, 10.0, now=0.0)
    perdant = eng.resolve(o2, Resolution(w, 99.0, 300.0), 0.92)
    assert not perdant.won and perdant.pnl == pytest.approx(-10.0)


def test_hold_s_est_en_temps_marche():
    """Corrige le bug `hold_s` d'origine, qui utilisait l'horloge murale."""
    from grokdesk.core.types import Resolution
    eng = PaperEngine(PaperVenue(latency_s=0.0))
    w = Window("BTC", 100.0, 1000.0, 1300.0)
    sig = Signal(w, Side.UP, 0.6, 0.92, break_even(0.92), 0.08, 0.01,
                 "forte", (), 1000.0)
    o = eng.place(sig, 10.0, now=1000.0)
    c = eng.resolve(o, Resolution(w, 101.0, 1300.0), 0.92)
    assert c.hold_s == pytest.approx(300.0)
