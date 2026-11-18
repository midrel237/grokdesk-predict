"""Tests de bout en bout : le harnais de mesure doit être honnête DANS LES
DEUX SENS.

Un harnais qui ne détecte jamais d'edge est inutile ; un harnais qui en
détecte toujours est dangereux. Ces deux tests verrouillent les deux côtés et
sont la garantie de non-régression la plus importante du dépôt.

C'est précisément le test que la version pump.fun aurait échoué : son résultat
de trade était tiré de `coin.quality` à l'instant de l'entrée, donc elle
« démontrait » un edge sur n'importe quelle configuration.
"""
from __future__ import annotations

import asyncio

import pytest

pytestmark = pytest.mark.slow

from grokdesk.core.config import load
from grokdesk.desk import run
from grokdesk.feeds.base import PricePath


def _run(drift: float, forecast_drift: float, hours: float = 24.0,
         mode: str = "window", seed: int = 7, payout: float = 0.92):
    cfg = load()
    cfg.prior.drift_per_s = forecast_drift
    # caps relevés pour obtenir un échantillon exploitable dans un test court
    cfg.risk.max_entries_per_hour = 60
    cfg.risk.daily_drawdown_stop = 0.99
    path = PricePath.gbm(asset="BTC", s0=100_000.0, vol_per_s=4e-4,
                         seconds=int(hours * 3600), dt=1.0,
                         drift_per_s=drift, seed=seed)
    return asyncio.run(run(path, cfg, mode=mode, window_s=300.0,
                           live_print=False, payout=payout))


# --------------------------------------------------------- contrôle négatif
def test_aucun_edge_revendique_sur_un_chemin_sans_derive():
    """Sur un GBM sans dérive, aucun edge n'existe. Le système ne doit pas en
    revendiquer un, et le verrou live doit rester fermé."""
    s = _run(drift=0.0, forecast_drift=0.0)
    rep = s["cal"].report(0.92)
    ok, _ = s["audit"].gate_live()

    assert rep.n > 50, "échantillon trop faible pour conclure quoi que ce soit"
    assert rep.verdict != "EDGE DEMONTRE", (
        f"edge revendiqué à tort : win rate {rep.win_rate:.4f}, "
        f"IC95 bas {rep.ci_low:.4f}, seuil {rep.break_even:.4f}")
    assert not ok, "le verrou live ne doit pas s'ouvrir sans edge réel"
    assert rep.ci_low <= rep.break_even


def test_le_pnl_reste_proche_de_la_marge_maison_sans_edge():
    """Sans edge, le P&L attendu est la marge de la maison, pas un hasard.

    Vérifie que le moteur ne fabrique pas de rendement : avec un win rate au
    voisinage du seuil d'équilibre, l'espérance par trade doit rester petite
    devant la mise.
    """
    s = _run(drift=0.0, forecast_drift=0.0)
    led = s["ledger"]
    assert led.closed, "aucun trade joué"
    moyenne_mise = sum(c.order.stake for c in led.closed) / len(led.closed)
    esperance = led.net_pnl / len(led.closed)
    assert abs(esperance) < 0.35 * moyenne_mise, (
        f"espérance {esperance:.4f} anormalement grande devant une mise "
        f"moyenne de {moyenne_mise:.4f} : le moteur fabrique du rendement")


# --------------------------------------------------------- contrôle positif
def test_edge_detecte_quand_il_existe_vraiment():
    """Avec une dérive réelle que PRIOR connaît, l'edge doit être détecté.

    Sans ce test, un harnais qui refuse systématiquement passerait le test
    négatif tout en étant inutilisable.
    """
    s = _run(drift=5e-6, forecast_drift=5e-6, hours=48.0)
    rep = s["cal"].report(0.92)

    assert rep.n > 100
    assert rep.win_rate > rep.break_even
    assert rep.verdict == "EDGE DEMONTRE", (
        f"edge réel non détecté : win rate {rep.win_rate:.4f} vs seuil "
        f"{rep.break_even:.4f}, IC95 [{rep.ci_low:.4f}, {rep.ci_high:.4f}]")
    assert rep.ci_low > rep.break_even
    assert rep.skill > 0
    assert s["ledger"].net_pnl > 0


def test_le_verrou_live_exige_aussi_la_calibration():
    """Le verrou est volontairement plus strict que le verdict.

    Sur ce run l'edge est démontré (IC95 bas ~0.59 > seuil 0.52) et pourtant
    le verrou reste fermé, parce que l'ECE dépasse 5 % : la probabilité
    annoncée s'écarte en moyenne de 5 points de la fréquence réalisée, ce qui
    suffit à fausser le dimensionnement de Kelly.

    Un edge réel mal mesuré ne doit pas ouvrir la porte à l'argent réel.
    """
    s = _run(drift=5e-6, forecast_drift=5e-6, hours=48.0)
    rep = s["cal"].report(0.92)
    ok, why = s["audit"].gate_live()

    assert rep.verdict == "EDGE DEMONTRE"
    if rep.ece > 0.05:
        assert not ok
        assert "calibration" in why, why
    else:
        assert ok, why
        assert "edge démontré" in why


def test_skill_de_prevision_nest_pas_un_edge():
    """La leçon centrale, encodée en test.

    À mi-fenêtre, la probabilité digitale a un vrai pouvoir prédictif (skill
    nettement positif) parce qu'elle sait que le prix s'est éloigné du strike.
    Et pourtant, sans dérive, le win rate reste au seuil d'équilibre : le
    payout mange exactement l'avantage informationnel.

    Avoir du skill et avoir un edge sont deux choses différentes.
    """
    s = _run(drift=0.0, forecast_drift=0.0)
    rep = s["cal"].report(0.92)
    assert rep.skill > 0.10, f"prévision sans pouvoir prédictif (skill {rep.skill:+.4f})"
    assert rep.verdict != "EDGE DEMONTRE"
    assert abs(rep.win_rate - rep.break_even) < 0.06


# ------------------------------------------------------ structure du produit
def test_mode_entry_nemet_aucun_signal():
    """Le résultat structurel : chez un broker binaire où le strike est le
    prix d'entrée, le moteur s'abstient sur 100 % des fenêtres."""
    s = _run(drift=0.0, forecast_drift=0.0, hours=6.0, mode="entry")
    assert s["edge"].emitted == 0
    assert s["edge"].abstained > 0
    assert not s["ledger"].closed


@pytest.mark.parametrize("payout", [0.70, 0.80, 0.92])
def test_mode_entry_nemet_rien_quel_que_soit_le_payout(payout):
    s = _run(drift=0.0, forecast_drift=0.0, hours=4.0, mode="entry",
             payout=payout)
    assert s["edge"].emitted == 0


def test_mode_entry_devient_jouable_avec_une_prevision_directionnelle():
    """Et la nuance : le mode entry n'est pas condamné en soi, il exige une
    compétence directionnelle. Avec une dérive connue, des signaux
    apparaissent — ce qui chiffre exactement ce qu'il faut savoir prédire."""
    s = _run(drift=2e-5, forecast_drift=2e-5, hours=12.0, mode="entry")
    assert s["edge"].emitted > 0


# --------------------------------------------------------------- intégrité
def test_le_bus_na_pas_avale_derreur():
    s = _run(drift=0.0, forecast_drift=0.0, hours=6.0)
    assert s["bus"].errors == [], f"erreurs dans les handlers : {s['bus'].errors}"


def test_comptabilite_coherente_en_fin_de_run():
    s = _run(drift=0.0, forecast_drift=0.0)
    led = s["ledger"]
    assert led.exposure == pytest.approx(0.0, abs=1e-6), \
        "toutes les positions doivent être résolues"
    attendu = led.start + sum(c.pnl for c in led.closed) - led.fees_paid
    assert led.equity == pytest.approx(attendu, abs=1e-4)
    assert s["risk"].open_positions == 0
