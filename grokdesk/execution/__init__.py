"""Exécution. `paper.py` est le moteur par défaut et le seul câblé tant que
`dry_run` est vrai dans la config.

Ce package remplace `market/broker.py`, dont le défaut n'était pas
l'implémentation mais le principe : l'ancien `runtime.on_entry()` tirait le
résultat du trade depuis `coin.quality` *à l'instant de l'entrée*, avant que
le prix ne bouge. Le book, la latence et le slippage n'influençaient donc
rien, et toute métrique mesurait le générateur aléatoire.

Ici le chemin de prix est produit par la source, en amont et indépendamment
des décisions, et la résolution est lue dessus. Un moteur papier ne peut pas
être plus favorable que la réalité par construction.
"""
