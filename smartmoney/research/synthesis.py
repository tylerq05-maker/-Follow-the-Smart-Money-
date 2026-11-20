"""Phase 5: the Disagreement Engine, combined signals, and 'Who moves first?'.

Panel: one row per (stock, 13F quarter), dated at the 13F filing deadline D — the first date on
which all three groups' behaviour over the preceding window is publicly knowable:
  insiders     : discretionary Form 4 purchases / sales FILED in (D-90, D]
  institutions : 13F changes for the quarter (public at D)
  crowd        : Wikipedia attention over (D-30, D-1] vs the prior 182 days (attention, not sentiment)
Outcome: excess return vs SPY from the close of the first trading day after D.

Interpretation guard-rails: associations, not causation; small cells are flagged, not ranked.
"""
from __future__ import annotations

import json
import logging
import time

import numpy as np
import pandas as pd
from scipy import stats as sps

from .. import config
from ..insiders.signals import Signal
from ..sources import factors, wikipedia
from . import insider_study as E
from . import returns as R
from .stats import clustered_mean

log = logging.getLogger(__name__)
OUT = config.RESULTS / "synthesis"
MIN_CELL = 30
_T = lambda e: pd.Series(True, index=e.index)  # noqa: E731

COMBOS: list[Signal] = [
    Signal("ALIGNED_BULL", "Insiders buying + institutions accumulating", 1, "combo", "x",
           "Insiders bought (>= $50k net, filed in prior 90 days) AND concentrated managers/breadth show accumulation.",
           "Independent informed groups agreeing should be stronger than either alone.", _T),
    Signal("ALIGNED_BEAR", "Insiders selling + institutions distributing", -1, "combo", "x",
           "Discretionary insider sales >= $1M with no purchases AND institutional distribution.", "Agreement on the downside.", _T),
    Signal("INS_BUY_INST_SELL", "Insiders buying, institutions distributing", 0, "combo", "x",
           "Insiders buying while institutions distribute.", "Disagreement: who is right?", _T),
    Signal("INS_SELL_INST_BUY", "Insiders selling, institutions accumulating", 0, "combo", "x",
           "Discretionary insider selling while institutions accumulate.", "Disagreement: who is right?", _T),
    Signal("INS_BUY_QUIET_CROWD", "Insiders buying, crowd not paying attention", 1, "combo", "x",
           "Insiders buying while Wikipedia attention is at or below normal.", "Information not yet attracting attention.", _T),
    Signal("INST_BUY_HOT_CROWD", "Institutions accumulating into crowd attention", 0, "combo", "x",
           "Institutional accumulation while attention is high.", "Are institutions riding retail attention?", _T),
    Signal("ALL_BULL", "All three bullish/attentive", 0, "combo", "x",
           "Insiders buying, institutions accumulating, attention high.", "Full agreement — or a crowded trade?", _T),
]
REGISTRY = {s.key: s for s in COMBOS}


# ------------------------------------------------------------------ panel
def build_panel() -> pd.DataFrame:
    from .institution_study import MIN_PREV_HOLDERS
    st = pd.read_parquet(config.PROCESSED / "f13_stock_events.parquet")
    st = st[st.investable & (st.n_prev_both >= MIN_PREV_HOLDERS) & (st.filing_date >= config.EVENT_START)].copy()
    dec = st.groupby("period")["breadth_change"].rank(pct=True)
    net = st.c_buyers - st.c_sellers
    st["inst_state"] = np.where((net >= 3) | (dec >= 0.9), "accumulating", np.where((net <= -3) | (dec <= 0.1), "distributing", "neutral"))

    ins = pd.read_parquet(config.PROCESSED / "insider_events_priced.parquet",
                          columns=["yahoo_symbol", "filing_date", "side", "group", "value", "plausible"])
    ins = ins[ins.plausible.fillna(False) & (ins.group == "insider")]
    buy_v, sell_v = np.zeros(len(st)), np.zeros(len(st))
    by = ins.groupby("yahoo_symbol")
    for sym, idx in st.groupby("yahoo_symbol").indices.items():
        if sym not in by.groups:
            continue
        g = by.get_group(sym)
        fd = g.filing_date.values.astype("datetime64[D]")
        for side, arr in (("buy", buy_v), ("sell", sell_v)):
            m = g.side.values == side
            d, v = fd[m], g.value.values[m]
            o = np.argsort(d)
            d, c = d[o], np.concatenate([[0.0], np.cumsum(v[o])])
            D = st.filing_date.values[idx].astype("datetime64[D]")
            arr[idx] = c[np.searchsorted(d, D, side="right")] - c[np.searchsorted(d, D - np.timedelta64(90, "D"), side="right")]
    st["ins_buy_90d"], st["ins_sell_90d"] = buy_v, sell_v
    st["ins_state"] = np.where((buy_v >= 50_000) & (buy_v >= sell_v), "buying",
                               np.where((sell_v >= 1_000_000) & (buy_v == 0), "selling", "neutral"))

    wm = wikipedia.ticker_articles().set_index("ticker")
    ticker_of = st.yahoo_symbol.str.replace("-", ".")
    att = np.full(len(st), np.nan)
    for t, idx in st.assign(t=ticker_of).groupby("t").indices.items():
        if t not in wm.index:
            continue
        v = wikipedia.load(t)
        if v is None or len(v) < 300:
            continue
        s = v.set_index("date")["views"].asfreq("D").fillna(0)
        cs = np.concatenate([[0.0], np.cumsum(s.values)])
        dates = s.index.values.astype("datetime64[D]")
        D = st.filing_date.values[idx].astype("datetime64[D]")
        a, b = np.searchsorted(dates, D - np.timedelta64(30, "D")), np.searchsorted(dates, D)
        c0 = np.searchsorted(dates, D - np.timedelta64(212, "D"))
        recent = (cs[b] - cs[a]) / np.maximum(b - a, 1)
        prior = (cs[a] - cs[c0]) / np.maximum(a - c0, 1)
        ok = (a - c0 >= 150) & (prior > 50)
        att[idx] = np.where(ok, recent / np.where(prior > 0, prior, np.nan), np.nan)
    st["attention_ratio"] = att
    st["crowd_state"] = np.where(np.isnan(att), "n/a", np.where(att >= 1.5, "high", np.where(att <= 0.67, "low", "normal")))
    return st


def _cell(d: pd.DataFrame, h: int = 60) -> dict:
    cm = clustered_mean(d[f"exc_{h}"].values, d["month"].values)
    x = d[f"exc_{h}"].dropna()
    return {"n": cm["n"], "mean": E._f(cm["mean"]), "t": E._f(cm["t"]), "p": E._f(cm["p"]),
            "median": E._f(x.median()) if len(x) else None, "hit_rate": E._f((x > 0).mean()) if len(x) else None,
            "sufficient": bool(cm["n"] >= MIN_CELL)}


def combination_tables(p: pd.DataFrame) -> dict:
    out = {"ins_x_inst": [], "three_way": [], "agreement": []}
    for (a, b), g in p.groupby(["ins_state", "inst_state"]):
        out["ins_x_inst"].append({"insiders": a, "institutions": b, **_cell(g),
                                  "oos": _cell(g[g["sample"] == "out_of_sample"])})
    q = p[p.crowd_state != "n/a"]
    for (a, b, c), g in q.groupby(["ins_state", "inst_state", "crowd_state"]):
        out["three_way"].append({"insiders": a, "institutions": b, "crowd": c, **_cell(g)})
    score = (p.ins_state.map({"buying": 1, "selling": -1, "neutral": 0}) + p.inst_state.map({"accumulating": 1, "distributing": -1, "neutral": 0}))
    for k, g in p.assign(score=score).groupby("score"):
        out["agreement"].append({"score": int(k), **_cell(g), "oos": _cell(g[g["sample"] == "out_of_sample"])})
    return out


def _ols_cluster(y, X, groups):
    ok = np.isfinite(y) & np.isfinite(X).all(1)
    y, X, groups = y[ok], X[ok], groups[ok]
    xtx = np.linalg.inv(X.T @ X)
    b = xtx @ X.T @ y
    e = y - X @ b
    _, inv = np.unique(groups, return_inverse=True)
    G = inv.max() + 1
    S = np.zeros((X.shape[1], X.shape[1]))
    U = np.zeros((G, X.shape[1]))
    np.add.at(U, inv, X * e[:, None])
    S = U.T @ U
    n, k = X.shape
    V = (G / (G - 1)) * ((n - 1) / (n - k)) * xtx @ S @ xtx
    se = np.sqrt(np.diag(V))
    return b, se, b / se, n


def horse_race(p: pd.DataFrame) -> dict:
    """Which group's state predicts? Pooled OLS of 60d excess return on state dummies, month-clustered SEs."""
    from .stats import winsorize
    names = ["insiders_buying", "insiders_selling", "inst_accumulating", "inst_distributing", "attention_high", "attention_low"]
    res = {}
    for sample, d in (("full", p), ("in_sample", p[p["sample"] == "in_sample"]), ("out_of_sample", p[p["sample"] == "out_of_sample"])):
        X = np.column_stack([np.ones(len(d)), d.ins_state.eq("buying"), d.ins_state.eq("selling"), d.inst_state.eq("accumulating"),
                             d.inst_state.eq("distributing"), d.crowd_state.eq("high"), d.crowd_state.eq("low")]).astype(float)
        y = winsorize(d["exc_60"].values.astype(float))
        b, se, t, n = _ols_cluster(y, X, d["month"].values)
        res[sample] = {"n": int(n), "intercept": E._f(b[0]),
                       "coefficients": [{"name": nm, "coef": E._f(b[i + 1]), "t": E._f(t[i + 1])} for i, nm in enumerate(names)]}
    return res


def combo_signals(p: pd.DataFrame) -> pd.DataFrame:
    ib, is_ = p.ins_state.eq("buying"), p.ins_state.eq("selling")
    ia, id_ = p.inst_state.eq("accumulating"), p.inst_state.eq("distributing")
    ch, cq = p.crowd_state.eq("high"), p.crowd_state.isin(["normal", "low"])
    masks = {"ALIGNED_BULL": ib & ia, "ALIGNED_BEAR": is_ & id_, "INS_BUY_INST_SELL": ib & id_, "INS_SELL_INST_BUY": is_ & ia,
             "INS_BUY_QUIET_CROWD": ib & cq, "INST_BUY_HOT_CROWD": ia & ch, "ALL_BULL": ib & ia & ch}
    se = pd.concat([p[m].assign(signal=k) for k, m in masks.items()], ignore_index=True)
    se["direction"] = se.signal.map(lambda k: REGISTRY[k].direction)
    se["event_id"] = se.signal + "-" + se.cusip + "-" + se.filing_date.dt.strftime("%Y%m%d")
    return se


# ------------------------------------------------------------------ who moves first
def _excess_path(s: R.Series, spy: R.Series, anchor: np.datetime64, lo: int, hi: int) -> np.ndarray | None:
    i = np.searchsorted(s.dates, anchor, side="right") - 1
    if i + lo < 0 or i + hi >= len(s.dates) or i < 0:
        return None
    seg = s.dates[i + lo:i + hi + 1]
    r = s.adj[i + lo:i + hi + 1] / s.adj[i] - 1
    b = R.bench_return(spy, np.repeat(s.dates[i], len(seg)), seg)
    return r - b


def event_time_profiles(spy: R.Series) -> dict:
    """Mean cumulative excess return (and abnormal attention) in trading-day event time, anchored at day 0."""
    out = {}
    ins = pd.read_parquet(config.PROCESSED / "insider_signal_events.parquet", columns=["signal", "yahoo_symbol", "filing_date", "sample"])
    inst = pd.read_parquet(config.PROCESSED / "f13_signal_events.parquet", columns=["signal", "yahoo_symbol", "filing_date", "period"])
    crowd = pd.read_parquet(config.PROCESSED / "crowd_signal_events.parquet", columns=["signal", "yahoo_symbol", "filing_date", "ticker", "day"])
    specs = [
        ("insider_cluster_buy", ins[ins.signal == "BUY_CLUSTER"], "filing_date", (-60, 60), "Form 4 filing date"),
        ("insider_ceo_cfo_sale", ins[ins.signal == "SELL_CEO_CFO"], "filing_date", (-60, 60), "Form 4 filing date"),
        ("hf_crowd_buy_vs_quarter", inst[inst.signal == "HF_CROWD_BUY"], "period", (-63, 100), "quarter end (13F public ≈ day +31)"),
        ("wiki_spike", crowd[crowd.signal == "WIKI_SPIKE"], "day", (-20, 40), "attention spike day"),
    ]
    rng = np.random.default_rng(0)
    for key, ev, col, (lo, hi), label in specs:
        ev = ev.sample(min(len(ev), 4000), random_state=1) if len(ev) > 4000 else ev
        mats = []
        for sym, g in ev.groupby("yahoo_symbol"):
            s = R.Series.load(sym)
            if s is None:
                continue
            for d in g[col].values.astype("datetime64[D]"):
                pth = _excess_path(s, spy, d, lo, hi)
                if pth is not None:
                    mats.append(pth)
        if not mats:
            continue
        M = np.vstack(mats)
        m, sd = np.nanmean(M, 0), np.nanstd(M, 0) / np.sqrt(np.isfinite(M).sum(0))
        out[key] = {"label": label, "n": int(len(M)), "days": list(range(lo, hi + 1)),
                    "mean": [E._f(x) for x in m], "lo": [E._f(x) for x in m - 1.96 * sd], "hi": [E._f(x) for x in m + 1.96 * sd]}
    # Abnormal attention around insider cluster buys (does the crowd notice insiders?).
    wm = set(wikipedia.ticker_articles().ticker)
    rows = []
    for r in ins[ins.signal == "BUY_CLUSTER"].itertuples():
        t = r.yahoo_symbol.replace("-", ".")
        if t not in wm:
            continue
        v = wikipedia.load(t)
        if v is None:
            continue
        s = v.set_index("date")["views"]
        base = s[r.filing_date - pd.Timedelta(days=120):r.filing_date - pd.Timedelta(days=31)].median()
        w = s[r.filing_date - pd.Timedelta(days=30):r.filing_date + pd.Timedelta(days=30)]
        if base and base > 20 and len(w) == 61:
            rows.append(np.log(np.maximum(w.values, 1) / base))
    if rows:
        A = np.vstack(rows)
        out["attention_around_insider_clusters"] = {"label": "Form 4 filing date (calendar days)", "n": len(A),
                                                    "days": list(range(-30, 31)), "mean": [E._f(x) for x in A.mean(0)]}
    return out


def attention_return_leadlag(spy: R.Series, max_stocks: int = 800, lags: int = 10) -> dict:
    """Pooled cross-correlation of daily abnormal attention and abnormal returns, plus per-stock Granger tests."""
    from statsmodels.tsa.stattools import grangercausalitytests
    wm = wikipedia.ticker_articles()
    tickers = [t for t in wm.ticker if (config.RAW / "wiki" / f"{t}.parquet").exists()]
    rng = np.random.default_rng(3)
    tickers = list(rng.choice(tickers, size=min(max_stocks, len(tickers)), replace=False))
    spy_r = pd.Series(spy.adj, index=pd.to_datetime(spy.dates)).pct_change()
    cc, g_att_ret, g_ret_att, n_ok = [], 0, 0, 0
    for t in tickers:
        s = R.Series.load(t.replace(".", "-"))
        v = wikipedia.load(t)
        if s is None or v is None:
            continue
        px = pd.Series(s.adj, index=pd.to_datetime(s.dates))
        ar = (px.pct_change() - spy_r.reindex(px.index)).loc["2016":]
        views = v.set_index("date")["views"]
        att = np.log(np.maximum(views, 1) / views.shift(1).rolling(60, min_periods=40).median())
        df = pd.concat([ar.rename("r"), att.rename("a")], axis=1, join="inner").dropna()
        if len(df) < 500 or df.a.std() == 0:
            continue
        n_ok += 1
        cc.append([df.a.corr(df.r.shift(-k)) for k in range(-lags, lags + 1)])  # k>0: attention today vs return k days later
        try:
            d2 = df[["r", "a"]].values
            p1 = grangercausalitytests(d2, maxlag=5, verbose=False)[5][0]["ssr_ftest"][1]          # a -> r
            p2 = grangercausalitytests(d2[:, ::-1], maxlag=5, verbose=False)[5][0]["ssr_ftest"][1]  # r -> a
            g_att_ret += p1 < 0.05
            g_ret_att += p2 < 0.05
        except Exception:
            pass
    C = np.array(cc)
    return {"n_stocks": n_ok, "lags": list(range(-lags, lags + 1)),
            "mean_corr": [E._f(x) for x in np.nanmean(C, 0)], "median_corr": [E._f(x) for x in np.nanmedian(C, 0)],
            "ci": [[E._f(a), E._f(b)] for a, b in zip(np.nanmean(C, 0) - 1.96 * np.nanstd(C, 0) / np.sqrt(len(C)),
                                                     np.nanmean(C, 0) + 1.96 * np.nanstd(C, 0) / np.sqrt(len(C)))],
            "granger": {"attention_causes_returns_share": E._f(g_att_ret / max(n_ok, 1)),
                        "returns_cause_attention_share": E._f(g_ret_att / max(n_ok, 1)), "null_rate": 0.05, "maxlag": 5},
            "interpretation_note": "Lag k > 0 correlates today's abnormal attention with the abnormal return k trading days later "
                                   "(attention leads); k < 0 means returns lead attention. Granger shares are compared with the 5% "
                                   "expected under no predictability; 'Granger causality' is predictive precedence, not causation."}


def scoreboard() -> list[dict]:
    """Every signal from every source on one table."""
    rows = []
    for mod, label in (("insiders", "Insiders"), ("institutions", "Institutions"), ("crowd", "Crowd"), ("synthesis", "Combined")):
        p = config.RESULTS / mod / "summary.json"
        if not p.exists():
            continue
        d = json.loads(p.read_text())
        for k, e in d["event_study"].items():
            f, o = e["full"]["60"], e["out_of_sample"]["60"]
            pf = d["portfolios"].get(k, {}).get("full", {})
            pl = d["placebo"].get(k, {})
            rows.append({"source": label, "key": k, "name": e["meta"]["name"], "direction": e["meta"]["direction"], "n": f.get("n"),
                         "mean_60": f.get("mean"), "t_60": f.get("t"), "oos_mean_60": o.get("mean"), "oos_t_60": o.get("t"),
                         "placebo_diff": pl.get("difference"), "placebo_p": pl.get("p_empirical"),
                         "ff6_alpha": (pf.get("ff6") or {}).get("alpha_ann"), "ff6_t": (pf.get("ff6") or {}).get("alpha_t"),
                         "net_sharpe": (pf.get("net") or {}).get("sharpe"), "holm_p": e.get("p_holm_primary")})
    return rows


def run() -> None:
    t0 = time.time()
    OUT.mkdir(parents=True, exist_ok=True)
    spy, iwm = R.Series.load(config.BENCHMARK), R.Series.load(config.SIZE_BENCHMARK)
    p = build_panel()
    p.to_parquet(config.PROCESSED / "synthesis_panel.parquet", index=False)
    se = combo_signals(p).reset_index(drop=True)
    study = E.event_study(se, REGISTRY)
    paths, placebo, port = E.pass_two(se, se[["yahoo_symbol", "filing_date"]], spy)
    pstats, curves = E.portfolios(port, spy, iwm, factors.load())
    summary = {
        "generated": pd.Timestamp.now().isoformat(timespec="seconds"),
        "config": {"horizons": E.H, "primary_horizon": E.PH, "event_start": config.EVENT_START, "in_sample_end": config.IN_SAMPLE_END,
                   "min_cell": MIN_CELL, "portfolio_hold_days": E.PORT_HOLD, "cost_tiers_bps": config.COST_TIERS,
                   "placebo_draws": E.PLACEBO_K, "cooldown_days": config.SIGNAL_COOLDOWN_DAYS, "price_data_end": str(spy.dates[-1]),
                   "min_price": config.MIN_PRICE, "min_median_dollar_volume": config.MIN_MEDIAN_DOLLAR_VOLUME,
                   "state_rules": {"insiders": "buying: >= $50k discretionary purchases filed in prior 90d and >= sales; selling: >= $1M discretionary sales, no purchases",
                                   "institutions": "accumulating: net >= 3 concentrated managers buying or top breadth decile; distributing: mirror",
                                   "crowd": "Wikipedia views prior 30d vs prior 182d: high >= 1.5x, low <= 0.67x"}},
        "panel": {"n": int(len(p)), "n_with_crowd": int((p.crowd_state != "n/a").sum()),
                  "state_counts": {c: p[c].value_counts().to_dict() for c in ("ins_state", "inst_state", "crowd_state")}},
        "signals": [{"key": s.key, "name": s.name, "direction": s.direction, "definition": s.definition, "rationale": s.rationale} for s in COMBOS],
        "tables": combination_tables(p), "horse_race": horse_race(p),
        "event_study": study, "placebo": E.placebo_test(se, placebo, REGISTRY), "portfolios": pstats, "robust_all_priced": {},
        "who_moves_first": {"profiles": event_time_profiles(spy), "attention_returns": attention_return_leadlag(spy)},
    }
    (OUT / "summary.json").write_text(json.dumps(summary, default=str))
    (OUT / "paths.json").write_text(json.dumps(E.mean_paths(se, paths)))
    (OUT / "curves.json").write_text(json.dumps(curves))
    (OUT / "scoreboard.json").write_text(json.dumps(scoreboard(), default=str))
    log.info("synthesis complete in %.0fs", time.time() - t0)
