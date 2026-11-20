"""Phase 2 research: does following institutional (13F) position changes predict returns?

Same machinery as the insider study (returns, clustered t, placebo, calendar-time FF6 portfolios),
applied to signals that are only actionable once the 13F information is public.
"""
from __future__ import annotations

import json
import logging
import time

import numpy as np
import pandas as pd

from .. import config
from ..insiders.signals import Signal, apply_cooldown
from ..institutions import build as B
from ..sources import factors
from . import insider_study as E
from . import returns as R
from .stats import clustered_mean

log = logging.getLogger(__name__)
OUT = config.RESULTS / "institutions"
SIGNAL_EVENTS_PATH = config.PROCESSED / "f13_signal_events.parquet"
MIN_PREV_HOLDERS = 5
_T = lambda e: pd.Series(True, index=e.index)  # noqa: E731

SIGNALS: list[Signal] = [
    Signal("INST_BREADTH_UP", "Breadth increase (top decile)", 1, "stock", "all",
           "Top cross-sectional decile of the change in the number of 13F managers holding the stock, among managers "
           "filing in both quarters, scaled by the number of such managers (Chen, Hong & Stein 2002). Dated at the "
           "45-day filing deadline.", "Broadening ownership signals improving consensus among informed investors.", _T),
    Signal("INST_BREADTH_DOWN", "Breadth decrease (bottom decile)", -1, "stock", "all",
           "Bottom decile of the same breadth change.", "Narrowing ownership: informed holders leaving.", _T),
    Signal("HF_CROWD_BUY", "Hedge-fund crowd buying", 1, "stock", "conc",
           "At least 3 concentrated managers opened or increased (>= 25%) the position and buyers exceed sellers by "
           ">= 3. Dated at the filing deadline.", "Multiple high-conviction managers accumulating the same stock.", _T),
    Signal("HF_CROWD_SELL", "Hedge-fund crowd selling", -1, "stock", "conc",
           "At least 3 concentrated managers exited or cut (>= 25%) and sellers exceed buyers by >= 3.",
           "Coordinated de-risking by concentrated managers.", _T),
    Signal("HF_ACCUM_2Q", "Two-quarter accumulation", 1, "stock", "conc",
           "Aggregate shares held by concentrated managers rose >= 10% in each of the last two quarters "
           "(split-adjusted, same managers).", "Persistent accumulation is harder to explain as noise.", _T),
    Signal("HF_NEW_CONVICTION", "New high-conviction position", 1, "manager", "conc",
           "A concentrated manager discloses a NEW position worth >= 3% of its 13F portfolio. Dated at that manager's "
           "own filing date.", "'Best ideas' of active managers outperform (Cohen, Polk & Silli 2010).", _T),
    Signal("NOTABLE_NEW", "Notable fund new position", 1, "manager", "notable",
           "One of a fixed list of well-known funds (Berkshire, Pershing Square, Appaloosa, …) discloses a new "
           "position >= 1% of its portfolio. Dated at its filing date.",
           "The popular 'clone the gurus' strategy, tested net of the 45-day disclosure lag.", _T),
]
REGISTRY = {s.key: s for s in SIGNALS}


def _issuer_id(df: pd.DataFrame) -> pd.Series:
    cik = pd.to_numeric(df["issuer_cik"], errors="coerce")
    # Unlisted-in-SEC-table issuers get a stable synthetic id from the CUSIP issuer prefix.
    synth = df["cusip"].str[:6].map(lambda s: -int.from_bytes(s.encode(), "big") % 10 ** 12)
    return cik.fillna(synth).astype("int64")


def stock_events(stock: pd.DataFrame) -> pd.DataFrame:
    s = stock.sort_values(["cusip", "period"]).copy()
    s["c_growth"] = s["c_shares"].fillna(0) / s["c_prev_shares"].replace(0, np.nan) - 1
    s["c_growth_prev"] = s.groupby("cusip")["c_growth"].shift()
    s["prev_period"] = s.groupby("cusip")["period"].shift()
    s.loc[s["prev_period"] != s["period"] - pd.offsets.QuarterEnd(1), "c_growth_prev"] = np.nan
    s["c_buyers"], s["c_sellers"] = s.c_new + s.c_incr, s.c_exit + s.c_decr
    s["filing_date"] = s["signal_date"]
    s["issuer_cik"] = _issuer_id(s)
    return s


def build_signals(st: pd.DataFrame, mev: pd.DataFrame) -> pd.DataFrame:
    out = []
    base = st[st.investable & (st.n_prev_both >= MIN_PREV_HOLDERS) & (st.filing_date >= config.EVENT_START)].copy()
    dec = base.groupby("period")["breadth_change"].rank(pct=True)
    out += [base[dec >= 0.9].assign(signal="INST_BREADTH_UP"), base[dec <= 0.1].assign(signal="INST_BREADTH_DOWN")]
    net = base.c_buyers - base.c_sellers
    out.append(base[(base.c_buyers >= 3) & (net >= 3)].assign(signal="HF_CROWD_BUY"))
    out.append(base[(base.c_sellers >= 3) & (-net >= 3)].assign(signal="HF_CROWD_SELL"))
    out.append(base[(base.c_growth >= 0.10) & (base.c_growth_prev >= 0.10) & (base.c_holders >= 2)].assign(signal="HF_ACCUM_2Q"))
    m = mev[mev.investable & (mev.filing_date >= config.EVENT_START)]
    for key, mask in (("HF_NEW_CONVICTION", m.concentrated & (m.weight >= 0.03)), ("NOTABLE_NEW", m.notable & (m.weight >= 0.01))):
        e = m[mask].sort_values("filing_date")
        e = e[apply_cooldown(e["filing_date"], e["issuer_cik"])]
        out.append(e.assign(signal=key))
    se = pd.concat(out, ignore_index=True)
    se["direction"] = se["signal"].map(lambda k: REGISTRY[k].direction)
    se["event_id"] = se["signal"] + "-" + se["cusip"] + "-" + se["filing_date"].dt.strftime("%Y%m%d")
    return se


def robustness(st: pd.DataFrame, mev: pd.DataFrame) -> list[dict]:
    rows = []
    base = st[st.investable & (st.n_prev_both >= MIN_PREV_HOLDERS) & (st.filing_date >= config.EVENT_START)]
    net = base.c_buyers - base.c_sellers
    variants = [("hf_crowd_min_buyers", k, base[(base.c_buyers >= k) & (net >= k)]) for k in (2, 3, 4, 5)]
    m = mev[mev.investable & mev.concentrated & (mev.filing_date >= config.EVENT_START)]
    for w in (0.01, 0.03, 0.05, 0.10):
        e = m[m.weight >= w].sort_values("filing_date")
        variants.append(("new_position_min_weight", w, e[apply_cooldown(e["filing_date"], e["issuer_cik"])]))
    for param, val, e in variants:
        for sample in ("in_sample", "out_of_sample"):
            d = e[e["sample"] == sample]
            cm = clustered_mean(d[f"exc_{E.PH}"].values, d["month"].values)
            rows.append({"param": param, "value": val, "sample": sample, "n": cm["n"], "mean": E._f(cm["mean"]), "t": E._f(cm["t"])})
    return rows


def lookahead_diag(st: pd.DataFrame, spy, iwm, data_end) -> dict:
    """What a backtest that (wrongly) trades 13F changes at quarter end would report."""
    base = st[st.investable & (st.n_prev_both >= MIN_PREV_HOLDERS) & (st.filing_date >= config.EVENT_START)]
    net = base.c_buyers - base.c_sellers
    crowd = base[(base.c_buyers >= 3) & (net >= 3)][["cusip", "yahoo_symbol", "period", "filing_date"]].copy()
    naive = E.returns_for(crowd.assign(qe=crowd["period"]), spy, iwm, data_end, date_col="qe")
    proper = base.loc[crowd.index]
    out = {}
    for h in (20, 60):
        out[f"naive_quarter_end_{h}d"] = E._f(naive[f"exc_{h}"].mean())
        out[f"proper_deadline_{h}d"] = E._f(proper[f"exc_{h}"].mean())
    out["n"] = int(len(crowd))
    out["median_lag_days"] = float((crowd.filing_date - crowd.period).dt.days.median())
    return out


def known_answers(recent_only: bool = False) -> dict:
    """Berkshire Hathaway's reported Apple stake by quarter (13F, CUSIP 037833100)."""
    import duckdb
    from ..sources import sec_13f
    con = duckdb.connect()
    df = con.execute(f"""SELECT period, min(filing_date) filing_date, sum(shares) shares, sum(value_usd) value_usd
                         FROM read_parquet('{sec_13f.glob()}') WHERE filer_cik = 1067983 AND cusip = '037833100'
                         GROUP BY period ORDER BY period""").df()
    con.close()
    return {"berkshire_aapl": [{"period": str(r.period.date()), "filing_date": str(r.filing_date.date()),
                                "shares": float(r.shares), "value_usd": float(r.value_usd)} for r in df.itertuples()]}


def run() -> None:
    t0 = time.time()
    OUT.mkdir(parents=True, exist_ok=True)
    spy, iwm = R.Series.load(config.BENCHMARK), R.Series.load(config.SIZE_BENCHMARK)
    data_end = spy.dates[-1]
    st = stock_events(pd.read_parquet(B.STOCK_PATH))
    st = E.returns_for(st, spy, iwm, data_end)
    mev = pd.read_parquet(B.MGR_EVENTS_PATH)
    mev["issuer_cik"] = _issuer_id(mev)
    mev = E.returns_for(mev, spy, iwm, data_end)
    log.info("returns attached in %.0fs", time.time() - t0)
    st.to_parquet(config.PROCESSED / "f13_stock_events.parquet", index=False)
    se = build_signals(st, mev).reset_index(drop=True)
    study = E.event_study(se, REGISTRY)
    paths, placebo, port = E.pass_two(se, se[["yahoo_symbol", "filing_date"]], spy)
    ff = factors.load()
    pstats, curves = E.portfolios(port, spy, iwm, ff)
    se.to_parquet(SIGNAL_EVENTS_PATH, index=False)
    mg = pd.read_parquet(B.MGR_PATH)
    cmap = pd.read_parquet(B.MAP_PATH)
    fl = pd.read_parquet(B.FILINGS_PATH)
    lag = (fl.filing_date - fl.period).dt.days
    summary = {
        "generated": pd.Timestamp.now().isoformat(timespec="seconds"),
        "config": {"horizons": E.H, "primary_horizon": E.PH, "event_start": config.EVENT_START,
                   "in_sample_end": config.IN_SAMPLE_END, "min_price": config.MIN_PRICE,
                   "min_median_dollar_volume": config.MIN_MEDIAN_DOLLAR_VOLUME, "portfolio_hold_days": E.PORT_HOLD,
                   "cost_tiers_bps": config.COST_TIERS, "placebo_draws": E.PLACEBO_K, "cooldown_days": config.SIGNAL_COOLDOWN_DAYS,
                   "price_data_end": str(data_end), "factor_data_end": str(ff.index.max().date()),
                   "concentrated_rule": {"max_positions": B.CONC_MAX_POSITIONS, "min_top10_weight": B.CONC_MIN_TOP10,
                                         "min_aum": B.CONC_MIN_AUM}, "min_prev_holders": MIN_PREV_HOLDERS},
        "signals": [{"key": s.key, "name": s.name, "direction": s.direction, "definition": s.definition,
                     "rationale": s.rationale} for s in SIGNALS],
        "event_study": study, "placebo": E.placebo_test(se, placebo, REGISTRY), "portfolios": pstats,
        "robustness_grid": robustness(st, mev), "robust_all_priced": {},
        "lookahead": lookahead_diag(st, spy, iwm, data_end),
        "coverage": {
            "n_filings": int(len(fl)), "n_managers": int(fl.filer_cik.nunique()),
            "periods": [str(fl.period.min().date()), str(fl.period.max().date())],
            "late_filings_share": float((~fl.on_time).mean()), "duplicate_originals": int(fl.dup.sum()),
            "filing_lag_days": {"median": float(lag.median()), "p90": float(lag.quantile(0.9)), "max_on_time": 45},
            "concentrated_share": float(mg.concentrated.mean()),
            "concentrated_per_period": int(mg[mg.concentrated].groupby("period").size().median()),
            "cusips_considered": int(len(cmap)), "cusip_mapping": cmap.method.value_counts().to_dict(),
            "stock_periods": int(len(st)), "stock_periods_investable": int(st.investable.sum()),
        },
        "known_answers": known_answers(),
        "breadth_by_period": st.groupby("period").agg(n=("cusip", "size"), filers=("n_filers_both", "first"))
                               .reset_index().assign(period=lambda d: d.period.dt.strftime("%Y-%m-%d")).to_dict("records"),
    }
    (OUT / "summary.json").write_text(json.dumps(summary, default=str))
    (OUT / "paths.json").write_text(json.dumps(E.mean_paths(se, paths)))
    (OUT / "curves.json").write_text(json.dumps(curves))
    log.info("institution study complete in %.0fs: %d signal events", time.time() - t0, len(se))
