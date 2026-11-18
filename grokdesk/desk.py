"""Orchestrateur du moteur de signaux. `python -m grokdesk.desk`

Deux modes de strike, et la différence entre les deux est l'argument central
du projet, démontrable en exécutant les deux :

* `--mode entry` — **broker binaire type Pocket Option.** Le strike EST ton
  prix d'entrée. Donc au moment de décider, `log(spot/strike) = 0`, et PRIOR
  renvoie mécaniquement p ≈ 0.5 (très légèrement en dessous, à cause du drag
  de variance). L'edge vaut alors `0.5 - 1/(1+payout)`, soit −2,1 % à 92 % de
  payout : **négatif par construction, pour tout payout < 100 %.** EDGE
  s'abstient sur 100 % des fenêtres, et c'est le résultat correct. Le seul
  moyen d'avoir un edge est une vraie prévision directionnelle — ce que
  `--drift` permet de quantifier.

* `--mode window` — **marché d'événement type Polymarket.** Le strike est fixé
  à l'ouverture de la fenêtre et ne bouge plus. On peut donc entrer plus tard,
  quand le spot s'est éloigné du strike, et la probabilité n'est plus 0.5. Si
  le prix du contrat n'a pas encore suivi, l'edge est réel.

C'est la même formule dans les deux cas. Seule la structure du produit change,
et elle décide s'il existe quelque chose à gagner.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import date, timedelta

from .agents.audit import Audit
from .agents.closer import Closer
from .agents.edge import Edge, break_even
from .agents.kelly import Kelly
from .agents.spotter import Spotter
from .agents.taker import Taker
from .core.bus import Bus
from .core.config import Config, load
from .core.ledger import Ledger
from .core.scoring import ScoringMatrix
from .core.types import (Closed, Side, Signal, Snapshot, Tick, Window)
from .execution.paper import PaperEngine, PaperVenue
from .feeds.base import PricePath, windows_over
from .model.calibration import Calibrator
from .model.prior import Prior, digital_p_up
from .report import perf
from .store.recorder import Recorder

BANNER = r"""
  ____ ____   ___  _  __  ____  _____ ____  _  __
 / ___|  _ \ / _ \| |/ / |  _ \| ____/ ___|| |/ /
| |  _| |_) | | | | ' /  | | | |  _| \___ \| ' /
| |_| |  _ <| |_| | . \  | |_| | |___ ___) | . \
 \____|_| \_\\___/|_|\_\ |____/|_____|____/|_|\_\
   moteur de signaux · SPOTTER PRIOR EDGE KELLY TAKER CLOSER
"""


def build(cfg: Config, matrix: ScoringMatrix | None = None,
          venue: PaperVenue | None = None) -> dict:
    """Câble le desk. Même rôle que l'ancien `runtime.build()`."""
    bus = Bus()
    bus.declare("tape", Snapshot)
    bus.declare("signal", Signal)
    bus.declare("closed", Closed)

    matrix = matrix or ScoringMatrix(min_edge=cfg.edge.min_edge)
    ledger = Ledger(cfg.bankroll)
    engine = PaperEngine(venue or PaperVenue())
    cal = Calibrator()

    from .risk.engine import RiskEngine
    risk = RiskEngine(ledger=ledger, cfg=cfg.risk)

    spotter = Spotter(bus, cfg.prior)
    prior = Prior(cfg.prior)
    edge = Edge(cfg.edge, matrix)
    kelly = Kelly(cfg.risk)
    taker = Taker(bus, engine, risk, ledger)
    closer = Closer(bus, engine, ledger, risk, cal)
    audit = Audit(bus, matrix, cal)

    bus.on("closed", audit.on_closed)

    return dict(bus=bus, cfg=cfg, matrix=matrix, ledger=ledger, engine=engine,
                cal=cal, risk=risk, spotter=spotter, prior=prior, edge=edge,
                kelly=kelly, taker=taker, closer=closer, audit=audit)


async def run(path: PricePath, cfg: Config, mode: str = "window",
              window_s: float = 300.0, live_print: bool = True,
              recorder: Recorder | None = None,
              payout: float = 0.92, decide_every: int = 5) -> dict:
    """Déroule le pipeline sur un chemin de prix pré-calculé.

    Le chemin existe **avant** la boucle et ne dépend d'aucune décision : le
    moteur ne peut pas être favorisé par construction.
    """
    s = build(cfg)
    bus, spotter, prior, edge, kelly = (
        s["bus"], s["spotter"], s["prior"], s["edge"], s["kelly"])
    taker, closer, risk, ledger, audit = (
        s["taker"], s["closer"], s["risk"], s["ledger"], s["audit"])

    wins = windows_over(path, duration_s=window_s)
    day0 = date(2026, 1, 1)
    all_ticks = list(path.ticks())
    tick_at: dict[float, Tick] = {t.ts: t for t in all_ticks}

    if live_print:
        print(BANNER)
        print(f"{cfg.banner()}  mode={mode}  payout={payout:.0%}  "
              f"seuil d'équilibre={break_even(payout):.4f}")
        print(f"{len(wins)} fenêtres de {window_s:.0f}s  "
              f"bankroll {cfg.bankroll:.2f}\n")

    hour_mark = -1
    for wi, w in enumerate(wins):
        today = day0 + timedelta(days=int(w.opened_ts // 86400))
        hour = int(w.opened_ts // 3600)
        if hour != hour_mark:
            hour_mark = hour
            risk.entries_this_hour = 0
        risk.check_kill_switch(today)

        # --- déroulé de la fenêtre, tick par tick ------------------------
        in_window = [t for t in all_ticks
                     if w.opened_ts <= t.ts <= w.expiry_ts]
        placed = None
        # Point de mesure de la calibration : à mi-fenêtre, pas à l'ouverture.
        # À l'ouverture, `p` vaut structurellement 0.5 (le spot est au strike),
        # donc une série prise là n'a aucune dispersion et son Brier ne mesure
        # rien. À mi-fenêtre la prévision est informative et comparable d'une
        # fenêtre à l'autre.
        p_mid = None
        mid_ts = w.opened_ts + 0.5 * window_s
        for k, tick in enumerate(in_window):
            spotter.ingest(tick)
            if k % decide_every or not spotter.vol.ready:
                continue
            now = tick.ts

            # le strike effectif dépend de la structure du produit
            if mode == "entry":
                eff = Window(asset=w.asset, strike=tick.microprice,
                             opened_ts=w.opened_ts, expiry_ts=w.expiry_ts,
                             venue=w.venue)
            else:
                eff = w

            snap = await spotter.publish(eff, now)
            if snap is None:
                continue
            fair = prior.fair(snap)
            if p_mid is None and now >= mid_ts:
                # en mode entry le strike suit le spot, donc on mesure la
                # prévision contre le strike *figé* de la fenêtre pour que la
                # calibration reste comparable entre les deux modes
                p_mid = digital_p_up(tick.microprice, w.strike,
                                     snap.vol_per_s,
                                     max(0.0, w.expiry_ts - now),
                                     drift_per_s=cfg.prior.drift_per_s)
            if recorder is not None:
                recorder.tick(tick)

            if placed is not None:
                continue
            sig = edge.evaluate(fair, snap, payout)
            if sig is None:
                continue
            ok_live, _ = audit.gate_live()
            sig = kelly.size(sig, calibrated=ok_live)
            await bus.emit("signal", sig)
            if recorder is not None:
                recorder.signal(sig)
            order = await taker.on_signal(sig, now, today, payout_at_fill=payout)
            if order is not None:
                order.fill_price = path.at(order.fill_ts or now)
                placed = order

        # --- résolution : lue sur le chemin, jamais tirée ----------------
        res = path.resolve(w)
        p_ref = p_mid if p_mid is not None else digital_p_up(
            path.at(mid_ts), w.strike, spotter.vol.value, 0.5 * window_s,
            drift_per_s=cfg.prior.drift_per_s)
        await closer.observe_unplayed(p_ref, res)
        if placed is not None:
            c = await closer.resolve(placed, path.resolve(placed.window),
                                     payout, today)
            if recorder is not None:
                recorder.closed(c)
        if recorder is not None:
            recorder.window(w, res.settle_price, res.outcome.value, p_ref)

        if live_print and (wi + 1) % 50 == 0:
            print(f"  f{wi + 1:>5}  signaux {edge.emitted:>4}  "
                  f"abstentions {edge.abstained:>6}  "
                  f"paris {len(ledger.closed):>4}  "
                  f"equity {ledger.equity:>10.2f}")

    _summary(s, payout, mode, len(wins), live_print)
    return s


def _summary(s: dict, payout: float, mode: str, n_windows: int,
             live_print: bool = True) -> None:
    edge, ledger, cal, audit, risk, matrix = (
        s["edge"], s["ledger"], s["cal"], s["audit"], s["risk"], s["matrix"])
    if not live_print:
        return
    print("\n" + "=" * 64)
    print(f"SCOREBOARD — mode={mode}  payout={payout:.0%}")
    print("=" * 64)
    print(f"  fenêtres observées : {n_windows}")
    print(f"  signaux émis       : {edge.emitted}")
    print(f"  abstentions        : {edge.abstained}")
    for r, n in sorted(edge.abstain_reasons.items(), key=lambda x: -x[1]):
        print(f"      {r:<28} {n}")
    print(f"  paris joués        : {len(ledger.closed)}")

    print("\n  --- performance ---")
    print(perf.summarise(ledger).render())

    print("\n  --- calibration PRIOR ---")
    print(cal.report(payout).render())

    ok, why = audit.gate_live()
    print(f"\n  verrou live        : {'OUVERT' if ok else 'FERMÉ'} — {why}")
    print(f"  révisions matrice  : {matrix.revisions}")
    if audit.log:
        for line in audit.log[-5:]:
            print(f"      {line}")
    st = risk.state()
    if st["frozen"]:
        print(f"  RISK GELÉ          : {'; '.join(st['freeze_reasons'])}")
    if st["blocks"]:
        print(f"  blocages risk      : {st['blocks']}")
    print()


def main() -> None:
    ap = argparse.ArgumentParser(
        prog="grokdesk.desk",
        description="Moteur de signaux binaires / marchés d'événement (papier).")
    ap.add_argument("--mode", choices=["entry", "window"], default="window",
                    help="entry = broker binaire (strike = entrée) ; "
                         "window = marché d'événement (strike figé à l'ouverture)")
    ap.add_argument("--payout", type=float, default=0.92,
                    help="payout du venue (0.92 = 92%%)")
    ap.add_argument("--hours", type=float, default=6.0, help="durée simulée")
    ap.add_argument("--window", type=float, default=300.0,
                    help="durée d'une fenêtre en secondes")
    ap.add_argument("--vol", type=float, default=0.0004,
                    help="volatilité par seconde du chemin de prix")
    ap.add_argument("--drift", type=float, default=0.0,
                    help="dérive réelle par seconde injectée dans le chemin "
                         "(0 = aucun edge possible, cas de contrôle)")
    ap.add_argument("--forecast-drift", type=float, default=0.0,
                    help="dérive que PRIOR suppose connaître : simule une "
                         "compétence directionnelle")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--record", action="store_true",
                    help="archiver dans DuckDB (data/market.duckdb)")
    ap.add_argument("--html", action="store_true",
                    help="rapport QuantStats dans data/perf.html")
    args = ap.parse_args()

    cfg = load()
    cfg.prior.drift_per_s = args.forecast_drift

    path = PricePath.gbm(asset="BTC", s0=100_000.0, vol_per_s=args.vol,
                         seconds=int(args.hours * 3600), dt=1.0,
                         drift_per_s=args.drift, seed=args.seed)

    rec = Recorder().open() if args.record else None
    s = asyncio.run(run(path, cfg, mode=args.mode, window_s=args.window,
                        recorder=rec, payout=args.payout))
    if rec is not None:
        rec.close()
        print(f"  archivé : {rec.written} → data/market.duckdb"
              if not rec.memory_only else
              "  duckdb absent : archivage en mémoire seulement")
    if args.html:
        p = perf.html_report(s["ledger"])
        print(f"  rapport : {p}" if p else
              "  quantstats/pandas absents : rapport HTML non généré")


if __name__ == "__main__":
    main()
