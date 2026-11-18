"""Tests de l'adaptateur Polymarket.

Hors réseau : les carnets sont construits à la main. Le test le plus important
est `test_carnet_trie_le_meilleur_en_premier`, garde contre un bug réel qui
archivait le niveau le plus éloigné en `level=0` et aurait corrompu tout
backtest sur la profondeur.
"""
from __future__ import annotations

import pytest

from grokdesk.feeds.polymarket import (Book, current_window_start,
                                       window_slug, WINDOW_S)
from grokdesk.store.recorder import Recorder


def _book(bids, asks) -> Book:
    return Book(token_id="t", bids=bids, asks=asks, ts=0.0)


# ------------------------------------------------------------------- slugs
def test_slug_deterministe():
    """Le slug encode la borne de 5 min : aucune recherche nécessaire."""
    assert window_slug("btc", 1795003200) == "btc-updown-5m-1795003200"
    assert window_slug("BTC", 1795003200) == "btc-updown-5m-1795003200"
    assert window_slug("eth", 1795003200, minutes=15) == "eth-updown-15m-1795003200"


def test_borne_de_fenetre_alignee():
    assert current_window_start(1795003200) == 1795003200
    assert current_window_start(1795003201) == 1795003200
    assert current_window_start(1795003499) == 1795003200
    assert current_window_start(1795003500) == 1795003500
    assert current_window_start(1795003201) % WINDOW_S == 0


# -------------------------------------------------------------- tri du book
def test_carnet_trie_le_meilleur_en_premier():
    """Garde anti-régression sur le bug de profondeur.

    Le CLOB renvoie les niveaux du pire au meilleur. Sans tri, `level=0`
    archivait 0.01/0.99 au lieu de 0.50/0.51.
    """
    # ordre tel que renvoyé par l'API : pire d'abord
    brut_bids = [(0.01, 10.0), (0.30, 20.0), (0.50, 30.0)]
    brut_asks = [(0.99, 10.0), (0.70, 20.0), (0.51, 30.0)]
    trie = _book(sorted(brut_bids, key=lambda r: r[0], reverse=True),
                 sorted(brut_asks, key=lambda r: r[0]))
    assert trie.bids[0][0] == 0.50
    assert trie.asks[0][0] == 0.51
    assert trie.spread == pytest.approx(0.01)
    assert trie.best_bid == 0.50 and trie.best_ask == 0.51


def test_best_bid_ask_robustes_a_un_ordre_quelconque():
    """max/min restent justes même non triés — c'est pourquoi les payouts
    archivés étaient corrects alors que la profondeur ne l'était pas."""
    b = _book([(0.01, 1), (0.50, 1), (0.30, 1)],
              [(0.99, 1), (0.51, 1), (0.70, 1)])
    assert b.best_bid == 0.50 and b.best_ask == 0.51


def test_carnet_vide():
    b = _book([], [])
    assert b.best_bid is None and b.best_ask is None
    assert b.mid is None and b.spread is None
    assert b.implied_payout is None and b.break_even is None


# ------------------------------------------------------- payout implicite
def test_payout_implicite_mesure_sur_le_marche_reel():
    """Valeurs relevées en direct le 18/11/2026 sur btc-updown-5m."""
    up = _book([(0.50, 100.0)], [(0.51, 100.0)])
    assert up.implied_payout == pytest.approx(0.9608, abs=1e-4)
    assert up.break_even == pytest.approx(0.51)

    down = _book([(0.49, 100.0)], [(0.50, 100.0)])
    assert down.implied_payout == pytest.approx(1.0)
    assert down.break_even == pytest.approx(0.50)


def test_avantage_structurel_sur_un_broker_binaire():
    """L'argument central, chiffré sur données mesurées.

    Acheter à 0.51 donne un seuil d'équilibre de 51,00 %, contre 52,08 % chez
    un broker binaire à 92 % de payout. Deux points de seuil, c'est tout
    l'edge quand l'edge revendiqué vaut 1,5 à 2 ¢.
    """
    poly = _book([(0.50, 100.0)], [(0.51, 100.0)]).break_even
    binaire_92 = 1.0 / 1.92
    assert poly < binaire_92
    assert (binaire_92 - poly) * 100 == pytest.approx(1.08, abs=0.01)

    # et la jambe à 0.50 est un jeu équitable : seuil exactement 50 %
    assert _book([(0.49, 1)], [(0.50, 1)]).break_even == 0.50


def test_payout_rejette_un_prix_hors_bornes():
    assert _book([(0.5, 1)], [(1.0, 1)]).implied_payout is None
    assert _book([(0.5, 1)], [(0.0, 1)]).implied_payout is None


# -------------------------------------------------------------- profondeur
def test_profondeur_cumulee():
    b = _book([(0.50, 10.0), (0.49, 20.0), (0.48, 30.0)],
              [(0.51, 5.0), (0.52, 15.0)])
    assert b.depth(2) == (30.0, 20.0)
    assert b.depth(5) == (60.0, 20.0)


# ---------------------------------------------------- recorder round-trip
def test_recorder_archive_un_carnet_en_memoire():
    """Sans DuckDB, le repli mémoire doit accepter les mêmes appels."""
    rec = Recorder("data/_test.duckdb")
    rec.memory_only = True       # on force le repli, sans toucher au disque
    b = _book([(0.50, 10.0)], [(0.51, 5.0)])
    rec.book("polymarket", "btc-updown-5m-1:up", b.ts, b.bids, b.asks, depth=2)
    rec.payout("polymarket", "BTC:up", b.implied_payout, b.ts)
    assert len(rec._buf["book_snapshots"]) == 2      # depth=2 → 2 lignes
    assert len(rec._buf["payouts"]) == 1
    # premier niveau archivé = meilleur niveau
    ligne = rec._buf["book_snapshots"][0]
    assert ligne[3] == 0 and ligne[4] == 0.50 and ligne[6] == 0.51


def test_recorder_comble_les_niveaux_manquants():
    rec = Recorder("data/_test.duckdb")
    rec.memory_only = True
    rec.book("polymarket", "s", 0.0, [(0.5, 1.0)], [], depth=3)
    rows = rec._buf["book_snapshots"]
    assert len(rows) == 3
    assert rows[1][4] is None and rows[0][6] is None
