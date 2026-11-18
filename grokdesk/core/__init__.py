"""Noyau agnostique du venue : bus, config, agents, scoring, ledger, types.

Rien dans ce package n'importe de dépendance externe. C'est volontaire : le
moteur de signaux et toute sa suite de tests tournent en stdlib pure, et les
bibliothèques tierces (duckdb, cryptofeed, ccxt, quantstats) vivent derrière
des adaptateurs à import paresseux dans `feeds/`, `store/` et `report/`.
"""
