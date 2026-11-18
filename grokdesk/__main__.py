from __future__ import annotations

import argparse
import asyncio

from .runtime import run


def main() -> None:
    ap = argparse.ArgumentParser(prog="grokdesk", description="Autonomous pump.fun trading desk (paper).")
    ap.add_argument("--hours", type=int, default=72, help="sim hours to run")
    ap.add_argument("--tph", type=int, default=40, help="ticks per hour")
    ap.add_argument("--start", type=float, default=5.0, help="starting wallet in SOL")
    ap.add_argument("--seed", type=int, default=0, help="rng seed (0 = random)")
    ap.add_argument("--delay", type=float, default=0.0, help="seconds between ticks for a live feel")
    ap.add_argument("--quiet", action="store_true", help="only print the final scoreboard")
    args = ap.parse_args()
    asyncio.run(run(hours=args.hours, tph=args.tph, start_sol=args.start,
                    seed=args.seed, live=not args.quiet, delay=args.delay))


if __name__ == "__main__":
    main()
