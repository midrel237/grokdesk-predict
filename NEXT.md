# Point d'étape — reprise

> Mis à jour le 18 novembre 2026. `IMPLEMENTATION.md` porte le plan complet ;
> ce fichier dit où on en est et ce qui bloque.

## Ça marche, vérifié

```bash
python -m pytest tests/ -q -m "not slow"        # 81 tests, 0.4 s
python -m pytest tests/ -q                      # 93 tests, ~4 min 30
python -m grokdesk.record --assets btc,eth --seconds 60   # recorder LIVE
python -m grokdesk.desk --mode entry            # broker binaire -> 0 signal
python -m grokdesk.desk --mode window --hours 24  # marché d'événement
python -m grokdesk --hours 12 --tph 20 --seed 42  # démo pump.fun, intacte
```

Le noyau tourne en **stdlib pure**, et le chemin lecture seule Polymarket
aussi : les tests et le recorder n'ont besoin de rien d'autre que DuckDB (et
même DuckDB est optionnel, avec repli mémoire).

## Données réelles mesurées (18/11/2026)

Le recorder a tourné contre Polymarket en direct : 2 080 lignes archivées,
**zéro erreur**.

| | Payout implicite | Seuil d'équilibre |
| --- | --- | --- |
| Polymarket `btc-updown-5m`, jambe Up à 0,51 | 96,1 % | **51,00 %** |
| Polymarket, jambe Down à 0,50 | 100 % | **50,00 %** |
| Broker binaire à 92 % | 92,0 % | **52,08 %** |

Soit **1 à 2 points de seuil d'avantage structurel** pour Polymarket. Quand
l'edge revendiqué vaut 1,5 à 2 ¢, c'est tout l'edge.

Deux autres faits mesurés, qui comptent autant :

- **Le spread est de 1 cent** au meilleur niveau, sur un contrat coté ~0,50 —
  soit ~2 % du notionnel pour traverser. L'edge revendiqué étant de 1,5 à 2 ¢,
  **le spread en mange la moitié à la totalité.** Conséquence de conception :
  il faut viser une exécution *maker* (poster au bid), pas *taker*. Ça change
  la machine à états de TAKER, et ce n'est pas encore pris en compte.
- **Le marché reprice en cours de fenêtre.** Relevé simultané : la fenêtre qui
  vient d'ouvrir cote 0,50/0,51 (pile ou face) pendant que celle à mi-parcours
  cote 0,92/0,08. C'est exactement la structure que `--mode window` modélise,
  et c'est là que l'edge peut exister.

## Résultats du moteur

| Run | Win rate | Seuil | Verdict | Verrou live |
| --- | --- | --- | --- | --- |
| `--mode entry` (strike = entrée) | — | 0,5208 | **0 signal émis** | fermé |
| Contrôle, sans dérive | 52,00 % | 0,5208 | INDETERMINE | fermé |
| Dérive réelle connue de PRIOR | 62,96 % | 0,5208 | EDGE DEMONTRE | fermé (ECE 5,2 %) |

1. **Mode `entry` : edge négatif par construction.** Strike = prix d'entrée →
   `log(spot/strike) = 0` → `p ≈ 0,5` → edge `= 0,5 − 1/(1+payout)`, négatif
   pour tout payout < 100 %. Démontré sur six payouts en test.
2. **Avoir du skill n'est pas avoir un edge.** Sur le contrôle, la prévision à
   mi-fenêtre a un skill de +0,29 et le win rate reste collé au seuil : le
   payout mange exactement l'avantage informationnel.
3. **Le harnais est honnête dans les deux sens** — il refuse un edge absent et
   détecte un edge présent. C'est la propriété centrale du dépôt.

## Corrections apportées à mes propres conclusions

- ❌ **« Python 3.15 est trop récent pour la stack »** — faux. duckdb, ccxt,
  pandas, quantstats, apscheduler, httpx et cryptofeed ont tous des wheels
  3.15. Seul PyYAML n'en a pas, et il est déjà optionnel (parseur de repli
  dans `core/config.py`). L'échec initial venait d'un `pip install` groupé qui
  tentait de compiler `cffi` et avortait toute la transaction ; installer avec
  `--only-binary=:all:` règle le problème.
- ❌ **« CCXT supporte Polymarket via `ccxt.prediction.polymarket` »** — faux.
  ccxt 4.5.64 expose 105 exchanges, aucun marché de prédiction, et ni attribut
  `prediction` ni `polymarket`. J'avais changé le plan sur la foi d'un résumé
  de documentation au lieu de la lib installée. `feeds/polymarket_ccxt.py`
  supprimé, remplacé par `feeds/polymarket.py` en accès direct.
- 🐛 **Bug de profondeur archivée.** Le CLOB renvoie les niveaux du pire au
  meilleur. Les archiver positionnellement mettait le niveau le plus éloigné
  en `level=0` : un spread apparent de 0,98 sur un marché coté 0,50/0,51. Les
  payouts restaient justes (calculés en max/min) mais toute analyse de
  profondeur aurait été corrompue. Corrigé à la source + test de
  non-régression.

## Blocage non résolu — décision à prendre

**pandas ne s'importe pas sur cette machine.**

```
ImportError: DLL load failed while importing ops:
Une stratégie de contrôle d'application a bloqué ce fichier.
```

Ce n'est pas un problème de packaging : une politique Windows de contrôle
d'application (Smart App Control / WDAC) bloque les `.pyd` compilés non
signés de pandas. Ça n'affecte que **QuantStats et le rapport HTML** — et
`report/perf.py:summarise()` calcule déjà drawdown, win rate, profit factor et
espérance sans aucune dépendance, donc rien n'est perdu fonctionnellement.

Trois options, et c'est un réglage de sécurité système, donc ton choix :

1. **Laisser tel quel** et se contenter du résumé interne. Recommandé pour
   l'instant : le rapport HTML n'est pas sur le chemin critique.
2. Désactiver Smart App Control. Irréversible sans réinstallation de Windows,
   et ça baisse la protection de toute la machine.
3. Installer pandas depuis une source signée (conda-forge) dans un
   environnement séparé.

## Prochaine action, dans l'ordre

1. **`git init` + premier commit.** Toujours pas versionné, et il y a
   maintenant ~25 fichiers de code à protéger.
2. **Lancer le recorder en continu** (`--poll 5`, sans `--seconds`), idéalement
   en tâche planifiée Windows pour qu'il survive aux redémarrages. Chaque jour
   sans lui est un jour de backtest perdu.
3. Rejouer le book archivé dans `execution/paper.py` via
   `PricePath.from_rows` — on a maintenant de la vraie donnée, le GBM n'est
   plus qu'un cas de contrôle.
4. **Repenser TAKER en maker.** Le spread de 1 cent mesuré rend l'exécution
   taker non rentable sur un edge de 1,5 ¢. C'est le changement de conception
   le plus important révélé par les données.
5. Brancher `assets/terminal.html` sur un WebSocket FastAPI + bouton kill
   switch (toujours absent).

## Ce qui n'est pas fait

- Aucune exécution réelle. `execution/live.py` n'existe pas ;
  `feeds/pocketoption.py` n'expose délibérément aucune méthode de placement.
- Pas de recorder en continu, donc l'archive se limite aux ~3 minutes de test.
- Exécution maker non modélisée (point 4 ci-dessus).
- Méta-couche GROK CORE non reportée. Elle doit rester sans autorité sur le
  risk engine, ce que `test_aucun_agent_ne_peut_lever_un_gel` verrouille déjà.
- Dashboard non relié au moteur Python.
