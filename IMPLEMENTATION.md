# Plan d'implémentation — Polymarket Up/Down, ancré sur le code grokdesk existant

> **Version** : 2.0 — révision de `trade.md` v1.0 pour tenir compte du code réellement présent
> **Date** : 17 novembre 2026
> **Cible** : SPOTTER → PRIOR → EDGE → KELLY → TAKER → CLOSER sur les marchés binaires
> Polymarket BTC/ETH/SOL Up-Down 5 min & 15 min.
> **Base de départ** : `grokdesk/` — 19 agents, 6 desks, ~1 000 lignes de Python stdlib,
> simulateur pump.fun en papier + dashboard HTML autonome de 506 Ko.

---

## 0. Les avertissements de `trade.md` restent valables

Rien de ce document ne les remplace. Edges de 1,5–2 ¢ extrêmement fins, performances X
non vérifiées, paper trading obligatoire d'abord, `DRY_RUN = True` par défaut, et tu es
seul responsable des pertes. S'ajoutent deux contraintes propres à ce code-ci :

- **grokdesk décide le résultat d'un trade avant que le marché bouge.** C'est l'anti-pattern
  le plus dangereux à ne pas porter (détail en §3). Tant qu'il est là, tout backtest mentira.
- **Zéro test dans le dépôt, et pas de dépôt du tout** (`git rev-parse` échoue). Avant toute
  ligne qui touche de l'argent : `git init` + pytest.

---

## 1. Diagnostic de la base existante

### 1.1 Ce qui se réutilise réellement

| Fichier | Verdict | Travail requis |
| --- | --- | --- |
| `bus.py` (25 l.) | **Garder — c'est l'actif principal** | Les « handoffs typés » de la Phase 5 de `trade.md` existent déjà. À durcir : payloads typés (dataclasses) au lieu de tuples, et `asyncio.gather(..., return_exceptions=True)` — aujourd'hui une exception dans un handler annule tous les autres handlers du topic. |
| `scoring.py` (46 l.) | **Garder — meilleure idée du projet** | `ScoringMatrix` + bornes `_CLAMP` mappe directement sur les seuils d'EDGE (edge min, spread max, bande de vol). L'auto-tuning borné est exactement le bon garde-fou. Ajouter la persistance : aujourd'hui l'historique meurt avec le process. |
| `desks/base.py` (32 l.) | Garder la structure | `Agent.credit(good, w)` est un nudge ±0,1 arbitraire. À remplacer par un scoring sur métriques réelles (§4.3). |
| `llm.py` (52 l.) | Garder le pattern, réécrire | Le fallback online/offline est bon. Mais `urllib.request.urlopen` est **bloquant** et appelé depuis `research.on_signal` qui est `async` : il gèle la boucle d'événements. Rédhibitoire sur des fenêtres de 5 min. → `httpx.AsyncClient`, timeouts, cache. |
| `ledger.py` (43 l.) | Garder les concepts | SOL → USDC, ajout des frais réels, et surtout réconciliation du drift contre l'état on-chain (`trade.md` §3 Phase 3). `sweep()` devient la gestion de bankroll. |
| `head.py` (64 l.) | Garder, mais **rétrograder** | GROK CORE peut rester comme méta-couche qui active/désactive des variantes de stratégie. Interdiction absolue de toucher au risk engine ou de passer en live sans ack humain. |
| `runtime.py:build()` | Garder le pattern de wiring | La fonction `build()` qui câble tout et renvoie un dict est propre et testable. |
| `assets/terminal.html` | **Garder — énorme gain** | 506 Ko d'UI déjà dessinée (mesh d'agents, tape, courbe P&L, roster, feed d'alertes). Aujourd'hui il s'auto-simule en JS sans aucun lien avec Python. Le brancher sur un vrai WebSocket rend la Phase 5 de `trade.md` presque gratuite. Ajouter le bouton kill switch, absent. |

### 1.2 Ce qui doit mourir

| Fichier / comportement | Pourquoi |
| --- | --- |
| `market/bonding_curve.py` | Courbe pump.fun. Aucun analogue : un marché binaire se résout à 0 ou 1. Déjà quasi décoratif aujourd'hui (`GRAD_SOL=85` jamais atteint, `curve.graduated` jamais lu). |
| `market/pumpfun.py` | Feed de mints synthétiques. Remplacé par les vrais feeds (§2.2). |
| `market/broker.py` | Le concept de paper broker reste, l'implémentation meurt. `settle(coin, pos, realized_mult)` n'a pas de sens ici : il faut une machine à états d'ordre (limit, FOK/FAK, partial fills, retries). |
| `runtime.py:on_entry()` | **Le plus important.** Le résultat du trade est tiré de `coin.quality` (vérité cachée) *au moment de l'entrée*, avant toute évolution de prix. Le P&L ne dépend donc pas du marché. À purger : le résultat doit venir de la résolution réelle de la fenêtre. |
| Le couplage `grads ≈ wins` | `runtime.py` force `graduated=True` dès que `mult >= 3.0`. Deux métriques présentées comme indépendantes alors qu'une est dérivée de l'autre. Ne pas reproduire ce genre de double comptage dans les métriques Phase 6. |
| `execution.py:29` | `traders[len(self.broker.open) % len(traders)]` avec RISK qui bloque à 3 positions → seuls AGENT 01/02/03 tradent, 4 agents sur 7 sont morts. Ne pas porter cette sélection dans TAKER. |
| `Trade.hold_s` | `time() - pos.ts` = horloge murale, pas temps simulé. Durées en microsecondes, sans signification. Sur Polymarket le temps-à-résolution est une variable *centrale* du pricing : à faire correctement. |
| `runtime.py:_hud` `[FROZEN]` | Affiché si `wallet < start_sol`, alors que le vrai gel est à 60 % du start. Un indicateur de risque qui ment. Même écart dans le docstring de `ledger.py`. |
| `config/desk.yaml` non lu | Jamais chargé par le code. Inacceptable quand ce fichier doit porter `DRY_RUN`, les caps et les seuils de kill switch. |
| `llm.score_narrative` offline | `sha256(symbol|deployer) % 1000` = bruit pur, décorrélé de `coin.quality`. À retenir comme leçon, cf. §4.2. |

### 1.3 Ce que grokdesk prouve involontairement

Dans grokdesk, le score « narrative » produit par le LLM est du bruit de hachage, et le seul
edge réel du desk vient d'une règle déterministe (`funder.endswith("rug")`). Le simulateur
est donc une démonstration que **le jugement quantitatif porte le P&L et que la couche LLM
est cosmétique**. À transposer directement : voir §4.2 sur PRIOR.

---

## 2. Architecture cible

### 2.1 Arborescence

`grokdesk/` est conservé comme package unique ; la partie agnostique devient `core/`, le
simulateur pump.fun est déplacé en `sims/` pour que la démo actuelle continue de tourner.

```
grokdesk/
  core/
    bus.py              ← existant, durci (payloads typés, return_exceptions)
    agent.py            ← ex desks/base.py
    scoring.py          ← existant + persistance
    ledger.py           ← existant, USDC + frais + drift
    head.py             ← existant, rétrogradé (pas d'autorité sur le risk)
    config.py           ← NOUVEAU : charge réellement le YAML
  llm/
    client.py           ← ex llm.py, en httpx async
  feeds/
    spot.py             ← NOUVEAU : Binance/Coinbase WS, microprice, horloge de staleness
    clob.py             ← NOUVEAU : Polymarket CLOB WS (book) + REST (/book, /price)
    gamma.py            ← NOUVEAU : découverte des marchés actifs, slugs btc-updown-5m-*
    recorder.py         ← NOUVEAU, PRIORITÉ 1 : archive book + spot + résolutions
  model/
    prior.py            ← NOUVEAU : fair probability (digitale), vol réalisée
    indicators.py       ← NOUVEAU : EMA13, Elder Ray, Net Power, imbalance de book
  agents/
    spotter.py  prior.py  edge.py  kelly.py  taker.py  closer.py
    audit.py            ← ex desks/audit.py, recalibré
    bankroll.py         ← ex desks/treasury.py
  execution/
    orders.py           ← NOUVEAU : machine à états d'ordre
    paper.py            ← NOUVEAU : rejoue le book enregistré, zéro tirage caché
    live.py             ← NOUVEAU : py-clob-client-v2, verrouillé derrière DRY_RUN
  risk/
    engine.py           ← ex desks/risk.py + kill switch + drawdown journalier
  store/
    schema.sql  repo.py ← NOUVEAU : SQLite
  api/
    server.py           ← NOUVEAU : FastAPI + WebSocket qui alimente terminal.html
  sims/pumpfun/         ← tout l'ancien market/ + desks/radar|research|execution
config/desk.yaml        ← étendu et enfin chargé
tests/                  ← NOUVEAU
```

### 2.2 Flux de données

Le bus existant garde sa forme ; seuls les topics changent.

```
feeds/spot.py ──┐
feeds/clob.py ──┴→ "tape"      → SPOTTER  (snapshot 1–5 s, détection de staleness)
                                    │ "snapshot"
                                 PRIOR    (fair probability de la fenêtre)
                                    │ "fair"
                                 EDGE     (fair vs book, seuils de ScoringMatrix)
                                    │ "ticket"
                                 KELLY    (Kelly fractionnaire 0,25×) ─→ risk/engine.py
                                    │ "order"
                                 TAKER    (limit, FOK/FAK, partial fills, retries)
                                    │ "fill"
                                 CLOSER   (résolution de fenêtre, PnL réel, sync ledger)
                                    │ "closed"
                                 AUDIT    (Brier/log loss sur PRIOR, edge réalisé vs attendu)
                                    │
                                 ScoringMatrix.tighten()  ← la boucle existante, conservée
```

### 2.3 Correspondance des agents

Les 6 desks de grokdesk tombent presque 1:1 sur les 6 agents de `trade.md`.

| grokdesk | `trade.md` | Transposition |
| --- | --- | --- |
| RADAR (SCOUT/FEED/SIGNAL) | **SPOTTER** | Polling multi-sources → abonnements WS. L'idée du « poller trop lent » (`stats["stale"]`) devient une vraie détection de lag de feed — un prix spot vieux de 5 s sur une fenêtre de 5 min est fatal, pas cosmétique. |
| RESEARCH (4 checks + matrice) | **PRIOR** + **EDGE** | À scinder. PRIOR = modèle de probabilité calibré. EDGE = comparaison au book, et c'est lui qui hérite de `ScoringMatrix` et de ses bornes. |
| RISK | **KELLY** + Risk Engine | `size_for()` → Kelly fractionnaire. Les caps existants sont bons, le kill switch manque. |
| EXECUTION (EXEC/SNIPER/ROUTER + 01..07) | **TAKER** | Les 7 traders sont un artefact de démo (dont 4 inertes, cf. §1.2). Un TAKER unique avec une vraie machine à états. |
| — | **CLOSER** | Entièrement nouveau. Aucun équivalent : grokdesk clôt par timeout de ticks. |
| AUDIT | AUDIT | Conservé, mais noté sur la calibration et non sur un nudge ±0,05. |
| TREASURY | Bankroll | Conservé tel quel, concepts valides. |
| GROK CORE | Méta | Conservé, sans autorité sur le risque ni sur le passage en live. |

---

## 3. Le changement non négociable : un paper engine honnête

C'est le point de bascule entre « dashboard stylisé » et système évaluable.

Aujourd'hui `runtime.on_entry()` tire `win = rng.random() < (0.12 si rug sinon 0.30 + 0.5*q)`
à l'instant de l'entrée, puis `broker.settle()` applique le multiple décidé. Conséquences :
le book n'influence rien, la latence n'influence rien, le slippage n'existe pas, et la qualité
des décisions des agents n'est pas mesurée — elle est postulée.

Le remplacement :

1. `feeds/recorder.py` archive en continu le book L2, le spot et les résolutions de fenêtre.
2. `execution/paper.py` rejoue cet enregistrement : un ordre est rempli **si et seulement si**
   le book enregistré l'aurait rempli, au prix et à la profondeur enregistrés, avec la latence
   mesurée entre décision et arrivée.
3. `CLOSER` lit la résolution réelle de la fenêtre (0 ou 1), pas un multiple tiré.

Sans cela, inutile de passer aux phases suivantes : les métriques de la Phase 6 de `trade.md`
(win rate, edge réalisé, slippage) seraient toutes des artefacts du générateur aléatoire.

---

## 4. Plan par phases, révisé

Les durées de `trade.md` sont conservées quand elles tiennent, ajustées quand la base change
la charge réelle.

### Phase 0 — Fondations + démarrage du recorder (2–4 j, au lieu de 1–3)

Allongée parce que le dépôt part de zéro côté outillage.

- `git init`, premier commit avec `DRY_RUN = True` déjà en place, `.gitignore` (déjà présent).
- pytest + CI locale. Premiers tests sur `bus.py` et `scoring.py` : ce sont les deux modules
  conservés tels quels, donc ceux à verrouiller en premier.
- `core/config.py` qui **charge réellement** `config/desk.yaml`, avec `DRY_RUN`, les caps, les
  seuils de kill switch. Plus aucune de ces valeurs en dur dans le code.
- Dériver les clés CLOB L2 depuis la signature L1 sur un wallet dédié.
- Tester les endpoints publics : Gamma (marchés actifs), CLOB `/book`, `/price`, WS market.
- **Mesurer les frais réels** sur les marchés Up/Down courts, par observation, sans supposer
  qu'ils sont nuls. Un edge de 1,5 ¢ ne survit pas à une hypothèse de frais fausse.
- **Lancer `feeds/recorder.py` en 24/7 dès ce jour-là.** Il n'existe pas d'archive publique du
  book L2 Polymarket : la donnée de backtest de la Phase 6 ne peut venir que de là, et elle
  s'accumule en temps réel. Tout retard ici retarde mécaniquement la Phase 6.
- Schéma SQLite + logs structurés.

### Phase 1 — Data layer & SPOTTER (3–5 j, inchangé)

- `feeds/spot.py` : WS Binance/Coinbase, microprice, référence Chainlink si accessible.
- `feeds/clob.py` : book best bid/ask + profondeur, reconnexion, séquencement, détection de gap.
- ~~`feeds/gamma.py`~~ → **`feeds/polymarket.py`, fait et vérifié en direct.**
  Le slug `{asset}-updown-5m-{borne_unix_5min}` est **déterministe** : il se
  construit depuis l'horloge, aucune découverte n'est nécessaire. La fenêtre
  courante et les deux suivantes existent déjà ; séries actives confirmées
  pour btc, eth et sol.
- `model/indicators.py` : EMA 13, Bull/Bear/Net Power comme demandé — **mais** voir §4.1.
- SPOTTER publie un snapshot toutes les 1–5 s sur le topic `tape`, avec un âge de feed par
  source. Si une source dépasse son budget de staleness, SPOTTER le signale et EDGE s'abstient.

### Phase 2 — PRIOR + EDGE (6–9 j, au lieu de 4–7)

Allongée : `trade.md` traite PRIOR et EDGE comme une seule phase, mais PRIOR est le seul
composant qui n'a aucun équivalent réutilisable dans grokdesk et c'est le cœur quantitatif.

- `model/prior.py` : fair probability de « spot au-dessus du strike dans T secondes ». C'est le
  prix d'une option digitale, pas une opinion : estimateur de vol réalisée haute fréquence
  (Parkinson / Garman-Klass ou réalisé à pas fin) + forme fermée du type `Φ(·/σ√T)`, avec T
  décroissant en continu jusqu'à la résolution.
- Calibration explicite dès le départ : reliability diagram, Brier score, log loss sur les
  fenêtres enregistrées. Un PRIOR non calibré rend EDGE sans objet.
- `agents/edge.py` : ne passe un ticket que si edge > seuil **après frais mesurés**, spread
  acceptable, profondeur suffisante au prix visé, et vol dans la bande autorisée. Les seuils
  vivent dans `ScoringMatrix` avec les bornes `_CLAMP` existantes.

#### 4.1 Sur Elder Ray / Net Power

`trade.md` le demande, et ouvre lui-même la porte à « une version plus robuste basée sur la
microstructure ». Position retenue : l'implémenter comme **filtre** (c'est peu coûteux et cela
permet de le comparer), sans en attendre de l'edge. Elder Ray est construit sur une EMA 13 :
c'est un indicateur retardé, et sur une fenêtre de 5 minutes le retard est du même ordre que
l'horizon de trade. L'edge exploitable est ailleurs : **le book binaire retarde le spot.**
Le système est pour l'essentiel un arbitrage statistique de latence entre le prix spot et le
CLOB, et c'est précisément pourquoi frais, latence et compétition le mangent. Prioriser donc
l'imbalance de book et le momentum de microprice, et garder Net Power comme garde-fou mesurable.

### Phase 3 — KELLY + Risk Engine (3–5 j, au lieu de 2–4)

Base réutilisable : `desks/risk.py` a déjà les caps et le flag `frozen`.

- `size_for()` → Kelly fractionnaire 0,25× (0,5× maximum), calculé sur l'edge *estimé par
  PRIOR calibré*, jamais sur l'edge brut. Kelly sur une probabilité mal calibrée sur-mise
  systématiquement.
- Caps durs conservés : % max du bankroll par trade, exposition totale simultanée, positions
  ouvertes max, entrées max par fenêtre.
- Kill switch : drawdown journalier > X %, pause après 3 journées rouges consécutives,
  surveillance du drift ledger. Déclenchable à la main depuis le dashboard.
- Le risk engine garde la primauté de grokdesk (« outranks the head »), et cette fois il faut
  un test qui le prouve : GROK CORE ne doit pas pouvoir lever un gel.
- Corriger l'indicateur menteur : l'état affiché doit être `risk.frozen`, pas un seuil parallèle.

### Phase 4 — TAKER + CLOSER (5–8 j, au lieu de 3–6)

Allongée parce que `PaperBroker` ne fournit rien de réutilisable ici.

- `execution/orders.py` : machine à états (placé → partiellement rempli → rempli → annulé →
  expiré), idempotence sur les retries, réconciliation des ordres orphelins au redémarrage.
- `execution/paper.py` : le rejeu honnête de §3.
- `execution/live.py` : `py-clob-client-v2`, verrouillé derrière `DRY_RUN`, limit préféré,
  FOK/FAK selon le besoin, rate limits respectés.
- `agents/closer.py` : surveillance de la résolution, PnL réel, sync ledger (positions, cash,
  frais), archivage complet du trade. Temps-à-résolution en temps marché, jamais en horloge
  murale (bug `hold_s` de §1.2).

### Phase 5 — Orchestration + dashboard (2–4 j, au lieu de 4–8)

**Réduite de moitié** : le bus existe, `terminal.html` existe.

- Formaliser les payloads du bus en dataclasses typées (le `types.py` actuel est un bon point
  de départ : `Signal`/`Candidate`/`Position`/`Trade` → `Tape`/`Fair`/`Ticket`/`Order`/`Fill`).
- `api/server.py` : FastAPI + WebSocket qui republie les topics du bus.
- Brancher `terminal.html` sur ce WebSocket en remplaçant ses générateurs JS internes.
  Les panneaux existants couvrent déjà presque toute la demande de `trade.md` : état des
  agents, book live, positions, PnL, logs. **À ajouter : le bouton kill switch**, absent, et
  un bandeau `DRY_RUN` / `LIVE` impossible à confondre.

### Phase 6 — Tests & paper (2–4 semaines minimum, inchangé)

- Backtest sur les données accumulées par le recorder depuis la Phase 0.
- Paper live 24/7 sur plusieurs centaines à milliers de fenêtres.
- Métriques : win rate, **edge moyen réalisé vs edge prédit** (le couple qui compte), slippage
  réel, frais, max drawdown, drift ledger, Brier score de PRIOR, et taux d'abstention pour
  staleness de feed.
- Aucune métrique dérivée d'une autre et présentée comme indépendante (leçon `grads ≈ wins`).

### Phase 7 — Live très progressif (inchangé)

Capital minimal, monitoring 24/7 + alertes, revue quotidienne. `DRY_RUN = False` est un commit
à part, explicite, réversible.

---

## 5. Stack : écarts avec `trade.md`

| Composant | `trade.md` | Révision | Raison |
| --- | --- | --- | --- |
| Langage | Python 3.11+ | Python 3.11+ | Le dépôt déclare 3.10+ et tourne ici en **3.15.2**. Relever `requires-python` à `>=3.11`. |
| Agents | xAI Grok / Grok Bot | Modèle quantitatif pour PRIOR, LLM en périphérie | §1.3 et §4.1 : le LLM ne price pas une digitale à 5 min. Le garder pour le commentaire de régime et la synthèse de logs. |
| HTTP | — | `httpx>=0.27` **async** | `llm.py` utilise `urllib` bloquant dans un chemin async. Déjà déclaré en extra `live` dans `pyproject.toml`, actuellement non installé. |
| Trading | SDK CLOB V2 officiel | **Lecture : Gamma + CLOB en direct (stdlib). Écriture : `py-clob-client` officiel.** | ⚠️ **Correction du 18/11/2026.** La v2 de ce document affirmait que CCXT supportait Polymarket via `ccxt.prediction.polymarket`. **C'est faux** : vérifié contre la lib installée, ccxt 4.5.64 expose 105 exchanges et aucun marché de prédiction, et il n'existe ni attribut `prediction` ni `polymarket`. L'adaptateur ccxt a été supprimé. Ce qui marche, testé en direct : Gamma `GET /markets?slug=` et CLOB `GET /book?token_id=` sans authentification — donc tout le chemin lecture seule tient en stdlib, sans dépendance. |
| Stockage | PostgreSQL ou SQLite | **DuckDB** pour le recorder, SQLite pour l'état | DuckDB est columnar et lit Parquet nativement : c'est le bon moteur pour des snapshots de book en append continu puis des scans analytiques de backtest. SQLite reste pour l'état transactionnel (ordres, positions). |
| Dashboard | FastAPI+WS ou Streamlit | FastAPI + WS | `terminal.html` est déjà écrit en HTML/JS/canvas. Streamlit obligerait à le jeter. |
| Dépendances | — | — | Le projet est à zéro dépendance aujourd'hui. Cette propriété se perd nécessairement ; garder `sims/pumpfun/` exécutable en stdlib pure. |

---

## 6. Roadmap révisée

| Étape | `trade.md` | Révision |
| --- | --- | --- |
| MVP paper (SPOTTER + PRIOR + EDGE + exécution simulée) | 2–3 semaines | **3–4 semaines** — Phase 2 et 4 plus lourdes, Phase 5 plus légère |
| Système complet paper stable | 4–6 semaines | 5–7 semaines |
| Premiers trades live | après validation paper | inchangé, et conditionné à un PRIOR calibré, pas seulement à un P&L positif |

Le recorder tourne dès le jour 1 : au moment où la Phase 6 commence, il y a déjà plusieurs
semaines de book archivé, ce qui est le seul moyen de ne pas attendre les données après coup.

---

## 7. Prochaines actions

Les 5 actions de `trade.md` §7, réordonnées pour cette base :

1. `git init` + pytest + `DRY_RUN` dans un `core/config.py` qui charge vraiment le YAML.
2. Écrire et lancer `feeds/recorder.py` en 24/7. C'est l'action la plus urgente du plan.
3. Purger le tirage caché de `runtime.on_entry()` et déplacer la démo pump.fun dans `sims/`.
4. `model/prior.py` + son harnais de calibration (Brier, reliability diagram).
5. Elder Ray / Net Power en filtre mesurable, à côté de l'imbalance de book.
6. Checklist de sécurité : kill switch testé, risk engine non contournable par GROK CORE,
   bandeau DRY_RUN/LIVE non ambigu dans le dashboard.

---

## 8. Note finale

`trade.md` note que la majorité des systèmes multi-agents présentés sur les réseaux sont
fortement stylisés. grokdesk en est un exemple utile et honnête : son README annonce une
boucle d'auto-amélioration, et cette boucle existe vraiment (`ScoringMatrix.tighten`, bornée),
mais le P&L qu'elle optimise est tiré au sort avant que le marché n'ouvre. L'infrastructure
est bonne, la mesure est fictive.

Tout l'intérêt de partir de cette base est donc d'en garder le squelette — bus, matrice de
scoring bornée, hiérarchie des desks, dashboard — en remplaçant la fiction par une mesure
réelle. L'ordre importe : le paper engine honnête (§3) avant toute optimisation de stratégie.
