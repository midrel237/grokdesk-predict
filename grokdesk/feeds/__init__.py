"""Sources de prix et de marché.

Toutes exposent la même interface `PriceSource` (`feeds/base.py`), ce qui rend
le moteur agnostique du venue : le même pipeline tourne sur un chemin de prix
rejoué, sur Binance via cryptofeed, sur Polymarket via CCXT, ou sur un flux
broker en lecture seule.

Les dépendances tierces sont importées **paresseusement**, à l'intérieur des
méthodes. Conséquence voulue : le noyau et les tests tournent en stdlib pure,
et une lib manquante ne casse que l'adaptateur concerné.
"""
from .base import PriceSource, PricePath

__all__ = ["PriceSource", "PricePath"]
