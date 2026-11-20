"""Phase 3 research (attention): do Wikipedia attention shocks predict returns?

Attention is not sentiment. A spike in page views says people are looking, not whether they are
buying, so the unconditional spike signal has NO pre-registered direction and is tested two-sided.
Directional hypotheses are only stated where the literature gives one:
  * attention after a sharp rally -> subsequent reversal (Barber & Odean 2008, attention-driven buying)
  * a slow, sustained rise in attention -> short-run price pressure (Da, Engelberg & Gao 2011)
Timing: views for UTC day d are treated as public on d + 1; entry is the close of the first
trading day after that.
"""
from __future__ import annotations

import json
import logging
import time

import numpy as np
import pandas as pd

from .. import config
from ..insiders.signals import Signal, apply_cooldown
from ..sources import factors, prices, sec_reference, wikipedia
from . import insider_study as E
from . import returns as R
from .stats import clustered_mean

log = logging.getLogger(__name__)
OUT = config.RESULTS / "crowd"
EVENTS_PATH = config.PROCESSED / "wiki_attention_events.parquet"
SIGNAL_EVENTS_PATH = config.PROCESSED / "crowd_signal_events.parquet"
SPIKE_RATIO, SPIKE_MIN_VIEWS, BASE_DAYS = 3.0, 1_000, 60
_T = lambda e: pd.Series(True, index=e.index)  # noqa: E731

SIGNALS: list[Signal] = [
    Signal("WIKI_SPIKE", "Attention spike (any)", 0, "attention", "wiki",
           f"Daily Wikipedia page views >= {SPIKE_RATIO:.0f}x the median of the prior {BASE_DAYS} days and >= "
           f"{SPIKE_MIN_VIEWS:,} views. No direction is assumed; tested two-sided.",
           "Attention measures interest, not sentiment.", _T),
    Signal("WIKI_SPIKE_RALLY", "Spike after a rally", -1, "attention", "wiki",
           "Attention spike following a >= 10% rise over the prior 5 trading days.",
           "Attention-grabbing winners attract uninformed buying that later reverses (Barber & Odean 2008).", _T),
    Signal("WIKI_SPIKE_SELLOFF", "Spike after a sell-off", 0, "attention", "wiki",
           "Attention spike following a >= 10% fall over the prior 5 trading days. No direction assumed.",
           "Separates news-driven attention on losers from attention on winners.", _T),
    Signal("WIKI_SPIKE_QUIET", "Spike without a price move", 0, "attention", "wiki",
           "Attention spike while the prior 5-day return is within ±5%.",
           "Attention not yet reflected in price — does price follow?", _T),
    Signal("WIKI_RISING", "Sustained attention build", 1, "attention", "wiki",
           "28-day average views >= 2x the prior 182-day average, with >= 500 views/day.",
           "Rising retail attention creates short-run buying pressure (Da, Engelberg & Gao 2011).", _T),
]
REGISTRY = {s.key: s for s in SIGNALS}


def attention_events() -> pd.DataFrame:
    m = wikipedia.ticker_articles()
    sec = sec_reference.sec_ticker_table().drop_duplicates("ticker").set_index("ticker")
    rows = []
    for t in m.ticker:
        v = wikipedia.load(t)
        if v is None or len(v) < 250:
            continue
        s = v.set_index("date")["views"].asfreq("D").fillna(0)
        base = s.shift(1).rolling(BASE_DAYS, min_periods=40).median()
        ratio = s / base.replace(0, np.nan)
        spike = (ratio >= SPIKE_RATIO) & (s >= SPIKE_MIN_VIEWS)
        m28 = s.rolling(28).mean()
        m182 = s.shift(28).rolling(182, min_periods=120).mean()
        rising = (m28 >= 2 * m182) & (m28 >= 500)
        for kind, mask in (("spike", spike), ("rising", rising)):
            d = s.index[mask.fillna(False).values]
            if len(d):
                rows.append(pd.DataFrame({"ticker": t, "day": d, "kind": kind, "views": s[d].values,
                                          "ratio": ratio[d].values, "baseline": base[d].values}))
    ev = pd.concat(rows, ignore_index=True)
    ev["filing_date"] = ev["day"] + pd.Timedelta(days=1)  # public the day after the UTC day closes
    ev["yahoo_symbol"] = ev["ticker"].map(sec_reference.yahoo_symbol)
    ev["issuer_cik"] = ev["ticker"].map(sec["cik"]).fillna(-1).astype("int64")
    ev.loc[ev.issuer_cik < 0, "issuer_cik"] = -ev.loc[ev.issuer_cik < 0, "ticker"].map(lambda x: abs(hash(x)) % 10 ** 9)
    return ev


def build_signals(ev: pd.DataFrame) -> pd.DataFrame:
    e = ev[ev.investable & (ev.filing_date >= "2016-01-01")]
    out = []
    sp, ri = e[e.kind == "spike"], e[e.kind == "rising"]
    for key, d in (("WIKI_SPIKE", sp), ("WIKI_SPIKE_RALLY", sp[sp.prior_ret_5d >= 0.10]),
                   ("WIKI_SPIKE_SELLOFF", sp[sp.prior_ret_5d <= -0.10]), ("WIKI_SPIKE_QUIET", sp[sp.prior_ret_5d.abs() <= 0.05]),
                   ("WIKI_RISING", ri)):
        d = d.sort_values("filing_date")
        out.append(d[apply_cooldown(d["filing_date"], d["issuer_cik"])].assign(signal=key))
    se = pd.concat(out, ignore_index=True)
    se["direction"] = se.signal.map(lambda k: REGISTRY[k].direction)
    se["event_id"] = se.signal + "-" + se.ticker + "-" + se.filing_date.dt.strftime("%Y%m%d")
    return se


def known_answers() -> dict:
    v = wikipedia.load("GME")
    if v is None:
        return {}
    s = v.set_index("date")["views"]
    base = s.shift(1).rolling(BASE_DAYS, min_periods=40).median()
    w = s["2021-01-15":"2021-02-10"]
    peak = w.idxmax()
    return {"gme_2021": {"peak_day": str(peak.date()), "peak_views": int(w.max()), "baseline": float(base[peak]),
                         "ratio": float(w.max() / base[peak]),
                         "series": [{"date": str(d.date()), "views": int(x), "baseline": float(base[d])}
                                    for d, x in s["2020-12-01":"2021-03-15"].items()]}}


def run() -> None:
    t0 = time.time()
    OUT.mkdir(parents=True, exist_ok=True)
    spy, iwm = R.Series.load(config.BENCHMARK), R.Series.load(config.SIZE_BENCHMARK)
    data_end = spy.dates[-1]
    ev = attention_events()
    missing = [s for s in ev.yahoo_symbol.unique() if not prices.path(s).exists()]
    if missing:
        prices.build_universe_prices(workers=6, symbols=missing)
    ev = E.returns_for(ev, spy, iwm, data_end)
    ev.to_parquet(EVENTS_PATH, index=False)
    se = build_signals(ev).reset_index(drop=True)
    study = E.event_study(se, REGISTRY)
    paths, placebo, port = E.pass_two(se, se[["yahoo_symbol", "filing_date"]], spy)
    ff = factors.load()
    pstats, curves = E.portfolios(port, spy, iwm, ff)
    se.to_parquet(SIGNAL_EVENTS_PATH, index=False)
    # Attention-ratio sensitivity (pre-registered threshold 3x).
    grid = []
    sp = ev[(ev.kind == "spike") & ev.investable & (ev.filing_date >= "2016-01-01")]
    for k in (3, 5, 10, 20):
        d = sp[sp.ratio >= k].sort_values("filing_date")
        d = d[apply_cooldown(d.filing_date, d.issuer_cik)]
        for sample in ("in_sample", "out_of_sample"):
            x = d[d["sample"] == sample]
            cm = clustered_mean(x[f"exc_{E.PH}"].values, x["month"].values)
            grid.append({"param": "spike_min_ratio", "value": k, "sample": sample, "n": cm["n"], "mean": E._f(cm["mean"]), "t": E._f(cm["t"])})
    wm = wikipedia.ticker_articles()
    summary = {
        "generated": pd.Timestamp.now().isoformat(timespec="seconds"),
        "config": {"horizons": E.H, "primary_horizon": E.PH, "event_start": "2016-01-01", "in_sample_end": config.IN_SAMPLE_END,
                   "min_price": config.MIN_PRICE, "min_median_dollar_volume": config.MIN_MEDIAN_DOLLAR_VOLUME,
                   "portfolio_hold_days": E.PORT_HOLD, "cost_tiers_bps": config.COST_TIERS, "placebo_draws": E.PLACEBO_K,
                   "cooldown_days": config.SIGNAL_COOLDOWN_DAYS, "price_data_end": str(data_end),
                   "factor_data_end": str(ff.index.max().date()), "spike_ratio": SPIKE_RATIO, "spike_min_views": SPIKE_MIN_VIEWS,
                   "baseline_days": BASE_DAYS},
        "signals": [{"key": s.key, "name": s.name, "direction": s.direction, "definition": s.definition, "rationale": s.rationale}
                    for s in SIGNALS],
        "event_study": study, "placebo": E.placebo_test(se, placebo, REGISTRY), "portfolios": pstats,
        "robustness_grid": grid, "robust_all_priced": {},
        "coverage": {"wikidata_mappings": int(len(wm)),
                     "tickers_downloaded": int(sum((config.RAW / "wiki" / f"{t}.parquet").exists() for t in wm.ticker)),
                     "tickers_with_events": int(ev.ticker.nunique()),
                     "note": "Pageviews are fetched from a throttled API; coverage below 100% is the unfetched tail of an "
                             "arbitrary Wikidata ordering (equal by exchange). Companies below the view thresholds never trigger.",
                     "spike_days": int((ev.kind == "spike").sum()), "rising_days": int((ev.kind == "rising").sum()),
                     "investable_share": float(ev.investable.mean())},
        "known_answers": known_answers(),
        "sources_status": {"wikipedia": "live", "reddit": "blocked: Reddit API returns 403 from this environment; "
                           "PullPush refuses automated agents; Arctic Shift archive search times out",
                           "stocktwits": "reachable (recent messages only)", "news_gdelt": "intermittent",
                           "google_trends": "not reachable (404/blocked)"},
    }
    (OUT / "summary.json").write_text(json.dumps(summary, default=str))
    (OUT / "paths.json").write_text(json.dumps(E.mean_paths(se, paths)))
    (OUT / "curves.json").write_text(json.dumps(curves))
    log.info("crowd study complete in %.0fs: %d signal events", time.time() - t0, len(se))
