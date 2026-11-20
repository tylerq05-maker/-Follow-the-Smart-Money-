"""Command-line orchestration of the research pipeline.

    python -m smartmoney.pipeline ingest      # SEC bulk Form 3/4/5 -> data/processed/insider_tx
    python -m smartmoney.pipeline signals     # classify trades, build point-in-time events
    python -m smartmoney.pipeline reference   # CIK<->ticker map, company metadata
    python -m smartmoney.pipeline prices      # daily prices for the insider universe + benchmarks
    python -m smartmoney.pipeline insiders    # events, signals, forward returns, research results
    python -m smartmoney.pipeline all
"""
from __future__ import annotations

import argparse
import logging
import time
from concurrent.futures import ProcessPoolExecutor

from . import config

log = logging.getLogger("smartmoney")


def _ingest_one(yq):
    from .sources import sec_insider_bulk as b
    y, q = yq
    dest = b.OUT_DIR / f"{y}q{q}.parquet"
    if dest.exists():
        return yq, "cached"
    z = b.download(y, q)
    if z is None:
        return yq, "unpublished"
    df = b.normalise_quarter(z)
    b.OUT_DIR.mkdir(parents=True, exist_ok=True)
    df.to_parquet(dest, index=False)
    return yq, len(df)


def cmd_ingest(args) -> None:
    from .sources import sec_insider_bulk as b
    qs = list(b.quarters())
    with ProcessPoolExecutor(args.workers) as ex:
        for yq, res in ex.map(_ingest_one, qs):
            log.info("ingest %sq%s -> %s", *yq, res)


def cmd_reference(args) -> None:
    from .sources import sec_reference
    sec_reference.build_reference()


def cmd_prices(args) -> None:
    from .sources import prices
    prices.build_universe_prices(workers=args.workers)


def cmd_signals(args) -> None:
    """Classified trades + point-in-time events + data-quality report (always written together)."""
    import json
    from .insiders import signals as sg
    tx, dq = sg.build_classified()
    (config.PROCESSED / "insider_dq.json").write_text(json.dumps(dq, default=str))
    sg.build_events(tx).to_parquet(sg.EVENTS_PATH, index=False)


def cmd_insiders(args) -> None:
    from .research import insider_study
    insider_study.run()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("command", choices=["ingest", "signals", "reference", "prices", "insiders", "all"])
    p.add_argument("--workers", type=int, default=3)
    a = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    t = time.time()
    steps = ["ingest", "signals", "reference", "prices", "insiders"] if a.command == "all" else [a.command]
    for s in steps:
        globals()[f"cmd_{s}"](a)
    log.info("done in %.0fs", time.time() - t)


if __name__ == "__main__":
    main()
