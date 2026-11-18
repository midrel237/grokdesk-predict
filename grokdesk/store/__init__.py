"""Persistance. `recorder.py` (DuckDB) archive le marché, `state.py` (SQLite)
tient l'état transactionnel.

Le découpage n'est pas cosmétique : ce sont deux charges opposées. Le recorder
écrit en append continu et se relit en scans analytiques massifs — colonnaire.
L'état des ordres se lit et s'écrit ponctuellement avec des garanties
transactionnelles — relationnel.
"""
