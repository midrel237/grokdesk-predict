"""Tests du recorder, dont la propriété qui a motivé le mode Parquet.

DuckDB en fichier prend un verrou exclusif : impossible d'interroger l'archive
pendant que le recorder écrit. Un archivage 24/7 qu'il faut arrêter pour
backtester ne sert à rien, d'où le mode Parquet par défaut.
"""
from __future__ import annotations

import pytest

from grokdesk.core.types import Window
from grokdesk.store.recorder import COLUMNS, Recorder

duckdb = pytest.importorskip("duckdb")


def _book_rows(rec: Recorder, symbol: str, n: int = 3) -> None:
    for i in range(n):
        rec.book("polymarket", symbol, 1000.0 + i,
                 [(0.50 - i * 0.01, 100.0)], [(0.51 + i * 0.01, 90.0)],
                 depth=2)


# ------------------------------------------------------------------ schéma
def test_colonnes_extraites_du_ddl():
    assert COLUMNS["book_snapshots"][:4] == ["ts", "venue", "symbol", "level"]
    assert "payout" in COLUMNS["payouts"]
    assert "break_even" in COLUMNS["payouts"]


def test_types_ddl_resolus():
    assert Recorder._ddl_type("payouts", "payout") == "DOUBLE"
    assert Recorder._ddl_type("payouts", "venue") == "VARCHAR"
    assert Recorder._ddl_type("book_snapshots", "level") == "INTEGER"


# ----------------------------------------------------------------- parquet
def test_parquet_ecrit_et_relit(tmp_path):
    rec = Recorder(tmp_path / "pq", fmt="parquet").open()
    assert not rec.memory_only
    _book_rows(rec, "btc-updown-5m-1:up", 4)
    rec.payout("polymarket", "BTC:up", 0.9608, 1000.0)
    rec.flush()
    rec.close()

    con = duckdb.connect()
    g = (tmp_path / "pq" / "book_snapshots" / "**" / "*.parquet").as_posix()
    n = con.execute(f"SELECT count(*) FROM read_parquet('{g}')").fetchone()[0]
    assert n == 8, "4 relevés x profondeur 2"
    # premier niveau = meilleur niveau
    best = con.execute(
        f"SELECT bid_px, ask_px FROM read_parquet('{g}') "
        "WHERE level=0 ORDER BY ts LIMIT 1").fetchone()
    assert best == (0.50, 0.51)
    con.close()


def test_lecture_pendant_ecriture(tmp_path):
    """LA propriété qui justifie le mode Parquet.

    Le recorder reste ouvert et continue d'écrire ; un lecteur indépendant
    doit pouvoir interroger l'archive sans rencontrer de verrou.
    """
    rec = Recorder(tmp_path / "pq", fmt="parquet").open()
    _book_rows(rec, "btc-updown-5m-1:up", 2)
    rec.flush()                      # premier lot sur disque

    g = (tmp_path / "pq" / "book_snapshots" / "**" / "*.parquet").as_posix()
    lecteur = duckdb.connect()       # recorder TOUJOURS ouvert
    n1 = lecteur.execute(f"SELECT count(*) FROM read_parquet('{g}')").fetchone()[0]
    assert n1 == 4

    _book_rows(rec, "btc-updown-5m-1:up", 3)
    rec.flush()                      # second lot, pendant que le lecteur vit
    n2 = lecteur.execute(f"SELECT count(*) FROM read_parquet('{g}')").fetchone()[0]
    assert n2 == 10, "le lecteur doit voir les nouvelles lignes"

    lecteur.close()
    rec.close()


def test_parquet_partitionne_par_jour(tmp_path):
    rec = Recorder(tmp_path / "pq", fmt="parquet").open()
    _book_rows(rec, "s", 1)
    rec.flush()
    rec.close()
    jours = list((tmp_path / "pq" / "book_snapshots").iterdir())
    assert len(jours) == 1
    assert len(jours[0].name) == 10 and jours[0].name[4] == "-"


def test_source_sql_selon_le_format(tmp_path):
    pq = Recorder(tmp_path / "pq", fmt="parquet")
    assert "read_parquet" in pq.source("ticks")
    db = Recorder(tmp_path / "m.duckdb", fmt="duckdb")
    assert db.source("ticks") == "ticks"


# ------------------------------------------------------------------ duckdb
def test_mode_duckdb_fonctionne_toujours(tmp_path):
    """Le mode fichier reste utile pour un lot ponctuel."""
    rec = Recorder(tmp_path / "m.duckdb", fmt="duckdb").open()
    _book_rows(rec, "s", 2)
    rec.flush()
    rec.close()
    con = duckdb.connect(str(tmp_path / "m.duckdb"), read_only=True)
    assert con.execute("SELECT count(*) FROM book_snapshots").fetchone()[0] == 4
    con.close()


def test_mode_duckdb_prend_un_verrou_exclusif(tmp_path):
    """Documente le comportement qui a motivé le mode Parquet.

    Le refus prend deux formes selon l'appelant, et les deux ont été
    rencontrées pour de vrai :
      * autre processus  -> IOException, « fichier utilisé par un autre
        processus » (le cas qui bloquait l'analyse du recorder en marche)
      * même processus   -> ConnectionException, « different configuration »
    On accepte donc les deux : ce qui compte est qu'aucune lecture ne passe.
    """
    rec = Recorder(tmp_path / "m.duckdb", fmt="duckdb").open()
    _book_rows(rec, "s", 1)
    rec.flush()
    with pytest.raises((duckdb.IOException, duckdb.ConnectionException)):
        duckdb.connect(str(tmp_path / "m.duckdb"), read_only=True)
    rec.close()
    # une fois fermé, la lecture redevient possible
    con = duckdb.connect(str(tmp_path / "m.duckdb"), read_only=True)
    con.close()


# --------------------------------------------------------------- fenêtres
def test_archive_une_resolution_de_fenetre(tmp_path):
    rec = Recorder(tmp_path / "pq", fmt="parquet").open()
    w = Window("BTC", 0.0, 1000.0, 1300.0, venue="polymarket")
    rec.window(w, 0.93, "UP", -1.0)
    rec.flush()
    rec.close()
    con = duckdb.connect()
    g = (tmp_path / "pq" / "windows" / "**" / "*.parquet").as_posix()
    row = con.execute(
        f"SELECT asset, outcome, settle_price FROM read_parquet('{g}')").fetchone()
    assert row == ("BTC", "UP", 0.93)
    con.close()


def test_flush_vide_ne_cree_pas_de_fichier(tmp_path):
    rec = Recorder(tmp_path / "pq", fmt="parquet").open()
    assert rec.flush() == 0
    assert not (tmp_path / "pq" / "book_snapshots").exists()
    rec.close()
