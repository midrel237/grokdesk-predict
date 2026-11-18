"""Tests de la couche quantitative : PRIOR, vol, calibration.

Les tests les plus importants du dépôt, parce qu'ils vérifient les propriétés
dont dépend la validité de tout le reste.
"""
from __future__ import annotations

import math

import pytest

from grokdesk.model.calibration import (Calibrator, brier,
                                        brier_skill_score, log_loss,
                                        expected_calibration_error,
                                        samples_needed, wilson_interval)
from grokdesk.model.prior import digital_p_up, norm_cdf
from grokdesk.model.vol import EwmaVol, parkinson_per_s


# ------------------------------------------------------------------- prior
def test_au_strike_la_proba_est_un_demi():
    """Le résultat décisif pour un broker binaire : quand le strike est le
    prix d'entrée, la probabilité est 0.5 (au drag de variance près)."""
    p = digital_p_up(spot=100.0, strike=100.0, vol_per_s=4e-4,
                     horizon_s=300.0, variance_drag=False)
    assert p == pytest.approx(0.5, abs=1e-12)


def test_drag_de_variance_pousse_sous_un_demi():
    """Le drag vaut -0.5*sigma*sqrt(T) en z, soit ici -0.00346 :
    p = Phi(-0.00346) ~= 0.49862. Assez petit pour ne rien changer à la
    conclusion (l'edge reste négatif), assez grand pour être vérifié."""
    vol, horizon = 4e-4, 300.0
    p = digital_p_up(100.0, 100.0, vol, horizon, variance_drag=True)
    attendu = norm_cdf(-0.5 * vol * math.sqrt(horizon))
    assert p == pytest.approx(attendu, rel=1e-12)
    assert 0.498 < p < 0.5


def test_monotonie_en_moneyness():
    base = dict(strike=100.0, vol_per_s=4e-4, horizon_s=300.0)
    assert (digital_p_up(spot=99.0, **base)
            < digital_p_up(spot=100.0, **base)
            < digital_p_up(spot=101.0, **base))


def test_horizon_nul_est_deterministe():
    assert digital_p_up(101.0, 100.0, 4e-4, 0.0) == 1.0
    assert digital_p_up(99.0, 100.0, 4e-4, 0.0) == 0.0


def test_vol_nulle_est_deterministe():
    assert digital_p_up(101.0, 100.0, 0.0, 300.0) == 1.0


def test_plus_de_vol_ramene_vers_un_demi():
    """Plus d'incertitude = moins d'information = proba plus proche de 0.5."""
    peu = digital_p_up(101.0, 100.0, 1e-4, 300.0)
    beaucoup = digital_p_up(101.0, 100.0, 1e-2, 300.0)
    assert abs(beaucoup - 0.5) < abs(peu - 0.5)


def test_norm_cdf_valeurs_connues():
    assert norm_cdf(0.0) == pytest.approx(0.5)
    assert norm_cdf(1.96) == pytest.approx(0.975, abs=1e-3)
    assert norm_cdf(-1.96) == pytest.approx(0.025, abs=1e-3)


def test_prior_rejette_des_prix_invalides():
    with pytest.raises(ValueError):
        digital_p_up(0.0, 100.0, 4e-4, 300.0)


# --------------------------------------------------------------------- vol
def test_ewma_retrouve_la_vol_dun_gbm():
    """Propriété essentielle : si l'estimateur est biaisé, PRIOR price la
    mauvaise distribution et l'edge calculé est fictif."""
    import random
    rng = random.Random(42)
    true_vol = 4e-4
    v = EwmaVol(halflife_s=600.0)
    p = 100.0
    for i in range(1, 20_001):
        p *= math.exp(true_vol * rng.gauss(0, 1))
        v.update(p, float(i))
    assert v.value == pytest.approx(true_vol, rel=0.15)


def test_ewma_ignore_les_ticks_hors_sequence():
    v = EwmaVol()
    v.update(100.0, 10.0)
    v.update(101.0, 11.0)
    avant = v.value
    v.update(102.0, 5.0)      # horodatage dans le passé
    assert v.value == avant


def test_ewma_est_planchee():
    v = EwmaVol(floor=1e-6)
    for i in range(1, 100):
        v.update(100.0, float(i))      # prix constant → vol nulle
    assert v.value == 1e-6


def test_ewma_pas_prete_au_demarrage():
    v = EwmaVol()
    v.update(100.0, 1.0)
    assert not v.ready, "un estimateur à 1 tick ne doit pas être exploité"


def test_parkinson_positif():
    assert parkinson_per_s([101, 102], [99, 100], 60.0) > 0


# ------------------------------------------------------------- calibration
def test_brier_et_skill():
    assert brier([1.0, 0.0], [1, 0]) == 0.0
    assert brier([0.5, 0.5], [1, 0]) == 0.25
    # un modèle parfait a un skill de 1
    assert brier_skill_score([1.0, 0.0, 1.0, 0.0], [1, 0, 1, 0]) == 1.0


def test_skill_nul_pour_du_bruit():
    """Le test que le score « narrative » de la version pump.fun aurait
    échoué : un prédicteur sans information n'a aucun skill."""
    import random
    rng = random.Random(1)
    n = 4000
    preds = [rng.random() for _ in range(n)]
    outcomes = [rng.randint(0, 1) for _ in range(n)]
    assert brier_skill_score(preds, outcomes) < 0.02


def test_log_loss_fini_aux_bornes():
    assert math.isfinite(log_loss([0.0], [1]))


def test_ece_nulle_si_bien_calibre():
    preds = [0.25] * 400 + [0.75] * 400
    outcomes = [1] * 100 + [0] * 300 + [1] * 300 + [0] * 100
    assert expected_calibration_error(preds, outcomes) < 0.01


def test_wilson_borne_dans_zero_un():
    lo, hi = wilson_interval(0, 10)
    assert lo >= 0.0 and hi <= 1.0
    lo, hi = wilson_interval(10, 10)
    assert hi <= 1.0


def test_wilson_se_resserre_avec_n():
    l1, h1 = wilson_interval(55, 100)
    l2, h2 = wilson_interval(550, 1000)
    assert (h2 - l2) < (h1 - l1)


def test_taille_echantillon_requise():
    """Le chiffre qui conditionne tout : distinguer 56 % d'un seuil à
    52,08 % demande plusieurs centaines de fenêtres."""
    be = 1.0 / 1.92
    n = samples_needed(0.56, be)
    assert 400 < n < 900, f"attendu quelques centaines, obtenu {n}"
    assert samples_needed(0.50, be) == -1, "pas d'edge → rien à démontrer"


def test_verdict_exige_la_borne_basse():
    """Un win rate ponctuel au-dessus du seuil ne suffit pas : l'intervalle
    de confiance doit exclure l'absence d'edge."""
    cal = Calibrator()
    for i in range(40):
        cal.record_bet(0.6, i % 2 == 0, 0.92)       # 50 % sur 40 paris
    assert cal.report(0.92).verdict in ("INDETERMINE", "PERDANT DEMONTRE")

    cal2 = Calibrator()
    for i in range(2000):
        cal2.record_bet(0.6, i % 10 < 7, 0.92)      # 70 % sur 2000 paris
    rep = cal2.report(0.92)
    assert rep.verdict == "EDGE DEMONTRE"
    assert rep.ci_low > rep.break_even
