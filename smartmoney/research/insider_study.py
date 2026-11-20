"""Phase 1 research: do insider trades predict returns?

Pipeline: events -> identity verification (price reconciliation) -> point-in-time
market features & forward returns -> signals -> event study (clustered t,
bootstrap CI, Holm) -> same-stock random-date placebo -> calendar-time
portfolios with costs and Fama-French 5 + momentum alpha -> composite score
with in-sample fitting and out-of-sample evaluation -> exports for the UI.
"""
from __future__ import annotations

import json
import logging
import time

import numpy as np
import pandas as pd

from .. import config
from ..insiders import signals as sg
from ..sources import factors, sec_reference
from . import returns as R
from .stats import holm, newey_west_ols, perf_stats, summarize

log = logging.getLogger(__name__)
OUT = config.RESULTS / "insiders"
SIGNAL_EVENTS_PATH = config.PROCESSED / "insider_signal_events.parquet"
ALL_EVENTS_PATH = config.PROCESSED / "insider_events_priced.parquet"
H = config.HORIZONS
PH = config.PRIMARY_HORIZON
PORT_HOLD = 60
PLACEBO_K = 200
RNG = np.random.default_rng(20240611)


def _cost_bps(dvol: np.ndarray) -> np.ndarray:
    out = np.full(len(dvol), config.COST_TIERS[-1][1])
    for thr, bps in reversed(config.COST_TIERS):
        out[np.nan_to_num(dvol) >= thr] = bps
    return out


def _f(x):
    """JSON-safe float."""
    if x is None:
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return None if not np.isfinite(x) else round(x, 6)


# ------------------------------------------------------------ identity check
def reconcile_symbols(ref: pd.DataFrame) -> pd.DataFrame:
    """Verify each CIK->symbol mapping against prices insiders reported paying.

    Ticker reuse (a delisted company's symbol later assigned to another
    security) would otherwise attach a stranger's price history to an issuer.
    A mapping is VERIFIED when >= 60% of checked trades executed within 20% of
    that day's close; FAILED otherwise. With < 3 checkable trades it is
    INSUFFICIENT and only kept when the SEC currently lists the symbol for the CIK.
    """
    tx = sg.load_classified(columns=["issuer_cik", "trans_date", "price", "invalid_date", "quality"])
    tx = tx[(tx.price > 0) & ~tx.invalid_date & (tx.quality != "excluded")]
    tx = tx.sample(frac=1.0, random_state=1).groupby("issuer_cik").head(200)  # up to 200 random trades/issuer
    by = tx.groupby("issuer_cik").indices
    rows = []
    for r in ref.itertuples():
        sym = r.yahoo_symbol
        res = {"issuer_cik": r.issuer_cik, "n_checked": 0, "share_within_20pct": None, "median_abs_dev": None}
        s = R.Series.load(sym) if isinstance(sym, str) else None
        if s is None or r.issuer_cik not in by:
            res["status"] = "no_prices" if s is None else "no_trades"
        else:
            t = tx.iloc[by[r.issuer_cik]]
            d = t["trans_date"].values.astype("datetime64[D]")
            i = s.at_or_before(d)
            ok = (i >= 0) & ((d - s.dates[np.maximum(i, 0)]) <= np.timedelta64(4, "D")) & (d <= s.dates[-1])
            ratio = t["price"].values[ok] / s.raw[i[ok]]
            res["n_checked"] = int(ok.sum())
            if ok.sum():
                dev = np.abs(ratio - 1)
                res["share_within_20pct"] = float((dev <= 0.2).mean())
                res["median_abs_dev"] = float(np.median(dev))
            if ok.sum() >= 3:
                res["status"] = "verified" if res["share_within_20pct"] >= 0.6 else "failed"
            else:
                res["status"] = "insufficient"
        rows.append(res)
    rec = ref.merge(pd.DataFrame(rows), on="issuer_cik", how="left")
    rec["usable"] = (rec.status == "verified") | ((rec.status == "insufficient")
                                                  & rec.symbol_method.str.startswith("sec_current"))
    return rec


# ------------------------------------------------------------ pass 1: returns
def attach_returns(events: pd.DataFrame, rec: pd.DataFrame, spy: R.Series, iwm: R.Series,
                   data_end: np.datetime64) -> pd.DataFrame:
    events = events[events.filing_date >= pd.Timestamp(config.EVENT_START) - pd.Timedelta(days=400)]
    events = events.merge(rec[["issuer_cik", "yahoo_symbol", "symbol_method", "status", "usable"]]
                          .rename(columns={"status": "symbol_status"}), on="issuer_cik", how="left")
    parts = []
    for sym, idx in events[events.usable.fillna(False)].groupby("yahoo_symbol").indices.items():
        sub = events[events.usable.fillna(False)].iloc[idx]
        s = R.Series.load(sym)
        if s is None:
            continue
        o = R.event_returns(s, sub["filing_date"].values, spy, iwm, data_end)
        parts.append(pd.DataFrame(o, index=sub.index))
    ret = pd.concat(parts)
    ev = events.join(ret, how="left")
    ev["has_price"] = ev["has_price"].fillna(False).astype(bool)
    for h in H:
        ev[f"trunc_{h}"] = ev[f"trunc_{h}"].astype("boolean").fillna(False).astype(bool)
        ev[f"exc_{h}"] = ev[f"ret_{h}"] - ev[f"spy_{h}"]
        ev[f"exc_iwm_{h}"] = ev[f"ret_{h}"] - ev[f"iwm_{h}"]
        ev[f"exc_beta_{h}"] = ev[f"ret_{h}"] - ev["beta"].clip(-1, 4).fillna(1.0) * ev[f"spy_{h}"]
    ev["investable"] = ev.has_price & (ev.raw_close_sig >= config.MIN_PRICE) & \
        (ev.med_dvol20 >= config.MIN_MEDIAN_DOLLAR_VOLUME)
    # Reported prices far from the market close (typos, private deals) or absurd sizes.
    dev = (ev["vwap"] / ev["raw_close_sig"] - 1).abs()
    ev["plausible"] = (dev <= 0.5) & (ev["value"] <= np.maximum(5e9, 250 * ev["med_dvol20"].fillna(0)))
    ev["month"] = ev["filing_date"].dt.to_period("M").astype(str)
    ev["sample"] = np.where(ev.filing_date <= pd.Timestamp(config.IN_SAMPLE_END), "in_sample", "out_of_sample")
    return ev


# ------------------------------------------------------------ event study
def event_study(se: pd.DataFrame) -> dict:
    out, p_primary = {}, {}
    for key, g in se.groupby("signal"):
        s = sg.SIGNAL_BY_KEY[key]
        res = {}
        for sample, gg in (("full", g), ("in_sample", g[g["sample"] == "in_sample"]),
                           ("out_of_sample", g[g["sample"] == "out_of_sample"])):
            res[sample] = {}
            for h in H:
                summ = summarize(gg[f"exc_{h}"].values, gg["month"].values, raw=gg[f"ret_{h}"].values)
                summ.update(mean_exc_iwm=_f(np.nanmean(gg[f"exc_iwm_{h}"])) if len(gg) else None,
                            mean_exc_beta=_f(np.nanmean(gg[f"exc_beta_{h}"])) if len(gg) else None,
                            n_truncated=int(gg[f"trunc_{h}"].sum()))
                res[sample][str(h)] = {k: (_f(v) if isinstance(v, (float, np.floating)) else v) for k, v in summ.items()}
        x = g[f"exc_{PH}"].dropna()
        top = x[x >= x.quantile(0.99)] if len(x) >= 100 else x.iloc[:0]
        res["skew"] = {"mean": _f(x.mean()), "median": _f(x.median()),
                       "top1pct_contribution": _f(top.sum() / len(x)) if len(x) else None,
                       "mean_ex_top1pct": _f(x[x < x.quantile(0.99)].mean()) if len(x) >= 100 else None,
                       "mean_log_excess": _f((np.log1p(g[f"ret_{PH}"]) - np.log1p(g[f"spy_{PH}"])).mean())}
        nt = g[~g[f"trunc_{PH}"]]
        res["robust_ex_truncated"] = summarize(nt[f"exc_{PH}"].values, nt["month"].values)
        # one-sided direction-aware p for the hypothesis (bullish: >0, bearish: <0)
        p_primary[key] = res["full"][str(PH)].get("p")
        yearly = g.assign(year=g.filing_date.dt.year).groupby("year").agg(
            n=(f"exc_{PH}", "count"), mean=(f"exc_{PH}", "mean"), median=(f"exc_{PH}", "median"))
        res["by_year"] = [{"year": int(y), "n": int(r.n), "mean": _f(r["mean"]), "median": _f(r["median"])}
                          for y, r in yearly.iterrows() if r.n > 0]
        res["meta"] = {"key": key, "name": s.name, "direction": s.direction, "definition": s.definition,
                       "rationale": s.rationale, "n_events": int(len(g)), "n_issuers": int(g.issuer_cik.nunique()),
                       "first": str(g.filing_date.min().date()), "last": str(g.filing_date.max().date())}
        out[key] = res
    adj = holm(p_primary)
    for k in out:
        out[k]["p_holm_primary"] = _f(adj[k])
    return out


# ------------------------------------------------------------ pass 2: paths, portfolio rows, placebo
def pass_two(se: pd.DataFrame, buy_events: pd.DataFrame, spy: R.Series):
    se = se.reset_index(drop=True)
    paths = np.full((len(se), len(R.PATH_DAYS)), np.nan, dtype="float32")
    placebo = np.full((len(se), PLACEBO_K), np.nan, dtype="float32")
    port = []
    start = np.datetime64(config.EVENT_START)
    sig_codes = {k: i for i, k in enumerate(sorted(se.signal.unique()))}
    for sym, idx in se.groupby("yahoo_symbol").indices.items():
        s = R.Series.load(sym)
        if s is None:
            continue
        sub = se.iloc[idx]
        e0 = np.searchsorted(s.dates, sub["entry_date"].values.astype("datetime64[D]"))
        e0 = np.clip(e0, 0, len(s.dates) - 1)
        paths[idx] = R.event_paths(s, e0, spy)
        # portfolio holding rows: returns on days e0+1 .. e0+PORT_HOLD
        dr = np.concatenate([[np.nan], s.adj[1:] / s.adj[:-1] - 1])
        cost = _cost_bps(sub["med_dvol20"].values)
        for k, (e, sc, c) in enumerate(zip(e0, sub["signal"].values, cost)):
            j = np.arange(e + 1, min(e + 1 + PORT_HOLD, len(s.dates)))
            if len(j):
                port.append(pd.DataFrame({"date": s.dates[j], "sym": sym, "ret": dr[j], "signal": sc, "cost": c}))
        # placebo: random pseudo-filing days for the SAME stock
        n = len(s.dates)
        med = pd.Series(s.dvol).rolling(20, min_periods=10).median().values
        elig = (s.dates >= start) & (np.arange(n) + 1 + PH < n) & (s.raw >= config.MIN_PRICE) & \
               (med >= config.MIN_MEDIAN_DOLLAR_VOLUME)
        real = np.searchsorted(s.dates, buy_events.loc[buy_events.yahoo_symbol == sym, "filing_date"]
                               .values.astype("datetime64[D]"))
        for rp in np.concatenate([real, e0 - 1]):  # exclude +-PH days around any real insider event
            elig[max(0, rp - PH):rp + PH + 1] = False
        pos = np.where(elig)[0]
        if len(pos) < 20:
            continue
        draws = RNG.choice(pos, size=(len(idx), PLACEBO_K))
        ent, ext = draws + 1, draws + 1 + PH
        r = s.adj[ext] / s.adj[ent] - 1
        b = R.bench_return(spy, s.dates[ent].ravel(), s.dates[ext].ravel()).reshape(r.shape)
        placebo[idx] = (r - b).astype("float32")
    return paths, placebo, (pd.concat(port, ignore_index=True) if port else pd.DataFrame())


def placebo_test(se: pd.DataFrame, placebo: np.ndarray) -> dict:
    out = {}
    for key, idx in se.reset_index(drop=True).groupby("signal").indices.items():
        real = se[f"exc_{PH}"].values[idx]
        pl = placebo[idx]
        ok = np.isfinite(real) & np.isfinite(pl).all(1)
        if ok.sum() < 20:
            continue
        rm, pm = real[ok].mean(), pl[ok].mean(0)
        d = sg.SIGNAL_BY_KEY[key].direction
        if d == 0:  # control group: two-sided test of 'no difference'
            p = (1 + (np.abs(pm - pm.mean()) >= abs(rm - pm.mean())).sum()) / (PLACEBO_K + 1)
        else:       # directional hypothesis: how often does random timing do at least as well?
            p = (1 + (d * (pm - rm) >= 0).sum()) / (PLACEBO_K + 1)
        out[key] = {"n": int(ok.sum()), "real_mean": _f(rm), "real_median": _f(np.median(real[ok])),
                    "placebo_mean": _f(pm.mean()), "placebo_p05": _f(np.quantile(pm, 0.05)),
                    "placebo_p95": _f(np.quantile(pm, 0.95)), "difference": _f(rm - pm.mean()),
                    "p_empirical": _f(p), "draws": [_f(x) for x in np.sort(pm)]}
    return out


def mean_paths(se: pd.DataFrame, paths: np.ndarray) -> dict:
    out = {}
    for key, idx in se.reset_index(drop=True).groupby("signal").indices.items():
        p = paths[idx]
        n = np.isfinite(p).sum(0)
        m = np.nanmean(p, 0)
        sd = np.nanstd(p, 0)
        out[key] = [{"day": int(d), "mean": _f(m[i]), "lo": _f(m[i] - 1.96 * sd[i] / np.sqrt(max(n[i], 1))),
                     "hi": _f(m[i] + 1.96 * sd[i] / np.sqrt(max(n[i], 1))), "n": int(n[i])}
                    for i, d in enumerate(R.PATH_DAYS)]
    return out


# ------------------------------------------------------------ calendar-time portfolios
def portfolios(port: pd.DataFrame, spy: R.Series, iwm: R.Series, ff: pd.DataFrame) -> tuple[dict, dict]:
    cal = spy.dates[spy.dates >= np.datetime64(config.EVENT_START)]
    cidx = {d: i for i, d in enumerate(cal)}
    spy_r = pd.Series(spy.adj, index=pd.to_datetime(spy.dates)).pct_change()
    iwm_r = pd.Series(iwm.adj, index=pd.to_datetime(iwm.dates)).pct_change()
    rf = ff["RF"]
    stats, curves = {}, {}
    for key, g in port.groupby("signal"):
        g = g.dropna(subset=["ret"]).drop_duplicates(["date", "sym"])
        g = g[g.date.isin(cidx)]
        g["ci"] = g["date"].map(cidx).astype(int)
        nt = g.groupby("ci")["sym"].transform("size")
        g["w"] = 1.0 / nt
        prev = g[["sym", "ci", "w"]].assign(ci=g["ci"] + 1).rename(columns={"w": "w_prev"})
        cur = g.merge(prev, on=["sym", "ci"], how="left").fillna({"w_prev": 0.0})
        trade = (cur["w"] - cur["w_prev"]).abs() * cur["cost"] / 1e4
        exits = prev.merge(g[["sym", "ci"]], on=["sym", "ci"], how="left", indicator=True)
        exits = exits[exits["_merge"] == "left_only"]
        exits = exits.merge(g[["sym", "cost"]].drop_duplicates("sym"), on="sym", how="left")
        cost_t = trade.groupby(cur["ci"]).sum().add(
            (exits["w_prev"] * exits["cost"] / 1e4).groupby(exits["ci"]).sum(), fill_value=0)
        gross = g.groupby("ci")["ret"].mean()
        nh = g.groupby("ci").size()
        idx = pd.to_datetime(cal)
        gross = gross.reindex(range(len(cal)))
        cost_t = cost_t.reindex(range(len(cal))).fillna(0.0)
        gross.index = cost_t.index = idx
        nh = nh.reindex(range(len(cal))).fillna(0)
        nh.index = idx
        rf_al = rf.reindex(idx).ffill().fillna(0.0)
        net = (gross - cost_t).where(nh > 0, rf_al)
        grs = gross.where(nh > 0, rf_al)
        turnover = ((cur["w"] - cur["w_prev"]).abs().groupby(cur["ci"]).sum().sum()
                    + exits["w_prev"].sum()) / (len(cal) / 252)
        res = {"avg_holdings": _f(nh[nh > 0].mean()), "pct_days_invested": _f((nh > 0).mean()),
               "pct_days_lt5_holdings": _f(((nh > 0) & (nh < 5)).mean()), "annual_turnover": _f(turnover),
               "avg_cost_drag": _f(cost_t.mean() * 252)}
        for sample, mask in (("full", np.ones(len(idx), bool)),
                             ("in_sample", idx <= pd.Timestamp(config.IN_SAMPLE_END)),
                             ("out_of_sample", idx > pd.Timestamp(config.IN_SAMPLE_END))):
            r = net[mask]
            res[sample] = {"net": {k: _f(v) if not isinstance(v, str) else v
                                   for k, v in perf_stats(r, rf_al, spy_r).items()},
                           "gross": {k: _f(v) if not isinstance(v, str) else v
                                     for k, v in perf_stats(grs[mask], rf_al, spy_r).items()}}
            fx = ff.reindex(r.index).dropna()
            y = (r.reindex(fx.index) - fx["RF"]).values
            ok = np.isfinite(y)
            if ok.sum() > 250:
                facs = ["Mkt_RF", "SMB", "HML", "RMW", "CMA", "MOM"]
                X = np.column_stack([np.ones(ok.sum()), fx[facs].values[ok]])
                m6 = newey_west_ols(y[ok], X, lags=10)
                m1 = newey_west_ols(y[ok], X[:, :2], lags=10)
                res[sample]["ff6"] = {"alpha_ann": _f(m6["coef"][0] * 252), "alpha_t": _f(m6["t"][0]),
                                      "alpha_p": _f(m6["p"][0]), "r2": _f(m6["r2"]), "n_days": int(m6["n"]),
                                      "loadings": {f: {"beta": _f(m6["coef"][i + 1]), "t": _f(m6["t"][i + 1])}
                                                   for i, f in enumerate(facs)}}
                res[sample]["capm"] = {"alpha_ann": _f(m1["coef"][0] * 252), "alpha_t": _f(m1["t"][0]),
                                       "beta": _f(m1["coef"][1])}
        stats[key] = res
        eq = pd.DataFrame({"signal": (1 + net).cumprod(), "spy": (1 + spy_r.reindex(idx).fillna(0)).cumprod(),
                           "iwm": (1 + iwm_r.reindex(idx).fillna(0)).cumprod(), "holdings": nh})
        eq = eq.iloc[::5]
        curves[key] = {"dates": [d.strftime("%Y-%m-%d") for d in eq.index],
                       **{c: [_f(v) for v in eq[c]] for c in eq.columns}}
    return stats, curves


# ------------------------------------------------------------ composite score
FEATURES = {
    "log_value": ("log10 $ value of the day's purchases", lambda e: np.log10(e["value"].clip(lower=1))),
    "cluster": ("distinct insiders buying in 30d (capped at 5)", lambda e: e["n_insiders_30d"].clip(upper=5)),
    "ceo_cfo": ("CEO or CFO among buyers", lambda e: (e["ceo_cfo_value"] > 0).astype(float)),
    "conviction": ("log(1 + % holdings increase), capped at 500%",
                   lambda e: np.log1p(e["max_holding_change"].replace(np.inf, 5).clip(upper=5).fillna(0))),
    "dip": ("negative prior 60-day return", lambda e: -e["prior_ret_60d"].clip(-0.8, 0.8).fillna(0)),
    "opportunistic": ("opportunistic (non-routine) insider", lambda e: (e["n_opportunistic"] > 0).astype(float)),
    "first_2y": ("first purchase in 2+ years", lambda e: e["any_first_in_2y"].astype(float)),
    "small": ("smaller / less liquid stock (−log10 dollar volume)", lambda e: -np.log10(e["med_dvol20"].clip(lower=1))),
}


def composite_score(se: pd.DataFrame) -> tuple[dict, pd.DataFrame]:
    """Insider Conviction Score: equal-weight (pre-registered) vs. in-sample fitted weights.

    Features are standardised with IN-SAMPLE moments only. Fitted weights are
    OLS coefficients of winsorised 60d excess return on standardised features,
    estimated on in-sample events; both scores are then evaluated out of sample.
    """
    from scipy.stats import spearmanr
    from .stats import clustered_mean, winsorize
    base = se[se.signal == "BUY_ANY"].copy()
    F = pd.DataFrame({k: f(base) for k, (_, f) in FEATURES.items()}, index=base.index).astype(float)
    ins = base["sample"] == "in_sample"
    mu, sd = F[ins].mean(), F[ins].std().replace(0, 1)
    Z = ((F - mu) / sd).fillna(0)
    y = base[f"exc_{PH}"]
    fit_mask = ins & y.notna()
    X = np.column_stack([np.ones(fit_mask.sum()), Z[fit_mask].values])
    beta = np.linalg.lstsq(X, winsorize(y[fit_mask].values), rcond=None)[0][1:]
    base["score_equal"] = Z.mean(1)
    base["score_fitted"] = Z.values @ beta
    ref = np.sort(base.loc[ins, "score_equal"].values)
    base["conviction_score"] = np.searchsorted(ref, base["score_equal"].values) / len(ref) * 100

    def evaluate(col, mask):
        d = base[mask & y.notna()]
        if len(d) < 100:
            return None
        q = pd.qcut(d[col].rank(method="first"), 5, labels=False)
        qs = []
        for i in range(5):
            dd = d[q == i]
            cm = clustered_mean(dd[f"exc_{PH}"].values, dd["month"].values)
            qs.append({"q": i + 1, "n": cm["n"], "mean": _f(cm["mean"]), "t": _f(cm["t"]),
                       "median": _f(dd[f"exc_{PH}"].median())})
        ic = spearmanr(d[col], d[f"exc_{PH}"]).statistic
        hi, lo = d[q == 4], d[q == 0]
        spread = hi[f"exc_{PH}"].mean() - lo[f"exc_{PH}"].mean()
        se_ = np.sqrt(hi[f"exc_{PH}"].var() / len(hi) + lo[f"exc_{PH}"].var() / len(lo))
        return {"quintiles": qs, "spearman_ic": _f(ic), "q5_minus_q1": _f(spread), "q5_minus_q1_t_iid": _f(spread / se_)}

    oos = base["sample"] == "out_of_sample"
    res = {"features": [{"key": k, "label": v[0], "weight_fitted": _f(b), "weight_equal": _f(1 / len(FEATURES)),
                         "ic_in_sample": _f(spearmanr(Z.loc[ins & y.notna(), k], y[ins & y.notna()]).statistic),
                         "ic_out_of_sample": _f(spearmanr(Z.loc[oos & y.notna(), k], y[oos & y.notna()]).statistic)}
                        for (k, v), b in zip(FEATURES.items(), beta)],
           "methods": {m: {"in_sample": evaluate(c, ins), "out_of_sample": evaluate(c, oos)}
                       for m, c in (("equal_weight", "score_equal"), ("fitted_in_sample", "score_fitted"))}}
    return res, base[["event_id", "score_equal", "score_fitted", "conviction_score"]]


def robustness_grid(eligible: pd.DataFrame) -> list[dict]:
    """Do results survive threshold choices? (Thresholds were not optimised.)"""
    from .stats import clustered_mean
    b = eligible[(eligible.side == "buy") & (eligible.group == "insider") & (eligible.value >= config.MIN_PURCHASE_VALUE)]
    rows = []
    variants = [("cluster_min_insiders", k, b["n_insiders_30d"] >= k) for k in (2, 3, 4, 5)] + \
               [("min_purchase_value", v, b["value"] >= v) for v in (1e5, 2.5e5, 5e5, 1e6, 5e6)]
    for param, val, mask in variants:
        e = b[mask]
        e = e[sg.apply_cooldown(e["filing_date"], e["issuer_cik"])]
        for sample in ("in_sample", "out_of_sample"):
            d = e[e["sample"] == sample]
            cm = clustered_mean(d[f"exc_{PH}"].values, d["month"].values)
            rows.append({"param": param, "value": val, "sample": sample, "n": cm["n"], "mean": _f(cm["mean"]),
                         "t": _f(cm["t"])})
    return rows


def recent_signals(se: pd.DataFrame, n: int = 300) -> list[dict]:
    """Latest signal events with the named insiders behind them (evidence)."""
    r = se.sort_values("filing_date", ascending=False).head(n)
    tx = sg.load_classified(columns=["issuer_cik", "filing_date", "owner_name", "role", "value", "quality", "accession"],
                            filters=[("issuer_cik", "in", r.issuer_cik.unique().tolist())])
    tx = tx[tx.quality.isin(["discretionary_buy", "discretionary_sell", "planned_sell"])]
    out = []
    for e in r.itertuples():
        side_q = {"buy": ["discretionary_buy"], "sell": ["planned_sell"] if e.group == "planned" else ["discretionary_sell"]}[e.side]
        w = tx[(tx.issuer_cik == e.issuer_cik) & (tx.filing_date == e.filing_date) & tx.quality.isin(side_q)]
        who = w.groupby(["owner_name", "role"]).value.sum().reset_index().sort_values("value", ascending=False)
        out.append({"signal": e.signal, "signal_name": sg.SIGNAL_BY_KEY[e.signal].name, "direction": int(e.direction),
                    "issuer_cik": int(e.issuer_cik), "issuer_name": e.issuer_name, "symbol": e.yahoo_symbol,
                    "filing_date": str(e.filing_date.date()), "entry_date": str(pd.Timestamp(e.entry_date).date()),
                    "value": _f(e.value), "n_insiders": int(e.n_insiders), "n_insiders_30d": int(e.n_insiders_30d),
                    "prior_ret_60d": _f(e.prior_ret_60d), "conviction_score": _f(getattr(e, "conviction_score", None)),
                    "exc_20": _f(e.exc_20), "exc_60": _f(e.exc_60),
                    "insiders": [{"name": x.owner_name, "role": x.role, "value": _f(x.value)} for x in who.head(6).itertuples()],
                    "accessions": sorted(w.accession.unique().tolist())[:6]})
    return out


# ------------------------------------------------------------ orchestration
def run() -> None:
    t0 = time.time()
    OUT.mkdir(parents=True, exist_ok=True)
    events = pd.read_parquet(sg.EVENTS_PATH)
    ref = sec_reference.load_reference()
    ref = ref[ref.issuer_cik.isin(events.issuer_cik)]
    rec = reconcile_symbols(ref)
    rec.to_parquet(config.PROCESSED / "symbol_reconciliation.parquet", index=False)
    log.info("reconciliation: %s", rec.status.value_counts().to_dict())

    spy, iwm = R.Series.load(config.BENCHMARK), R.Series.load(config.SIZE_BENCHMARK)
    data_end = spy.dates[-1]
    ev = attach_returns(events, rec, spy, iwm, data_end)
    ev.to_parquet(ALL_EVENTS_PATH, index=False)
    log.info("returns attached in %.0fs", time.time() - t0)

    eligible = ev[ev.has_price & ev.investable & ev.plausible & (ev.filing_date >= config.EVENT_START)]
    se = sg.signal_events(eligible)
    se = se.reset_index(drop=True)
    buy_events = ev[(ev.side == "buy") & ev.has_price]
    study = event_study(se)
    paths, placebo, port = pass_two(se, buy_events, spy)
    log.info("pass two in %.0fs", time.time() - t0)
    ff = factors.load()
    pstats, curves = portfolios(port, spy, iwm, ff)
    comp, scores = composite_score(se)
    se = se.merge(scores, on="event_id", how="left")
    se.to_parquet(SIGNAL_EVENTS_PATH, index=False)

    # All-priced (no investability filter) robustness at the primary horizon.
    allp = sg.signal_events(ev[ev.has_price & ev.plausible & (ev.filing_date >= config.EVENT_START)])
    robust_all = {k: summarize(g[f"exc_{PH}"].values, g["month"].values) for k, g in allp.groupby("signal")}

    # Look-ahead diagnostic: naive same-day entry vs. our next-day entry (1-day excess).
    b = se[se.signal == "BUY_ANY"]
    look = {"naive_entry_mean_1d_excess": _f((b.naive_ret_1 - b.naive_spy_1).mean()),
            "naive_n": int((b.naive_ret_1 - b.naive_spy_1).notna().sum()),
            "proper_entry_mean_1d_excess": _f(b.exc_1.mean()),
            "violations_entry_not_after_filing": int((se.entry_date <= se.filing_date).sum()),
            "violations_signal_price_after_filing": int((se.sig_date > se.filing_date).sum())}

    # Coverage / survivorship accounting.
    base = ev[(ev.filing_date >= config.EVENT_START) & (ev.side == "buy") & (ev.group == "insider")]
    cov = base.assign(year=base.filing_date.dt.year).groupby("year").agg(
        events=("event_id", "size"), priced=("has_price", "sum"), investable=("investable", "sum"))
    coverage = {
        "symbol_status": rec.status.value_counts().to_dict(),
        "symbol_method": rec.symbol_method.value_counts().to_dict(),
        "buy_events_total": int(len(base)), "buy_events_priced": int(base.has_price.sum()),
        "buy_events_investable": int(base.investable.sum()),
        "buy_events_implausible": int((base.has_price & ~base.plausible).sum()),
        "by_year": [{"year": int(y), **{k: int(v) for k, v in r.items()}} for y, r in cov.iterrows()],
        "reasons_unpriced": base.loc[~base.has_price].assign(
            reason=lambda d: np.where(d.usable.fillna(False), "no price on event date",
                                      d.symbol_status.fillna("unresolved"))).reason.value_counts().to_dict(),
    }
    dq = json.loads((config.PROCESSED / "insider_dq.json").read_text()) if (config.PROCESSED / "insider_dq.json").exists() else {}

    summary = {
        "generated": pd.Timestamp.now().isoformat(timespec="seconds"),
        "config": {"horizons": H, "primary_horizon": PH, "event_start": config.EVENT_START,
                   "in_sample_end": config.IN_SAMPLE_END, "min_price": config.MIN_PRICE,
                   "min_median_dollar_volume": config.MIN_MEDIAN_DOLLAR_VOLUME,
                   "cluster_window_days": config.CLUSTER_WINDOW_DAYS, "cooldown_days": config.SIGNAL_COOLDOWN_DAYS,
                   "portfolio_hold_days": PORT_HOLD, "cost_tiers_bps": config.COST_TIERS,
                   "placebo_draws": PLACEBO_K, "benchmark": config.BENCHMARK,
                   "price_data_end": str(data_end), "factor_data_end": str(ff.index.max().date())},
        "signals": [{"key": s.key, "name": s.name, "direction": s.direction, "definition": s.definition,
                     "rationale": s.rationale} for s in sg.SIGNALS],
        "event_study": study, "placebo": placebo_test(se, placebo), "portfolios": pstats,
        "composite": comp, "robustness_grid": robustness_grid(eligible),
        "robust_all_priced": {k: {kk: _f(vv) if isinstance(vv, (float, np.floating)) else vv for kk, vv in v.items()}
                              for k, v in robust_all.items()},
        "lookahead": look, "coverage": coverage, "data_quality": dq,
    }
    (OUT / "summary.json").write_text(json.dumps(summary, default=str))
    (OUT / "recent_signals.json").write_text(json.dumps(recent_signals(se), default=str))
    (OUT / "paths.json").write_text(json.dumps(mean_paths(se, paths)))
    (OUT / "curves.json").write_text(json.dumps(curves))
    log.info("insider study complete in %.0fs: %d signal events", time.time() - t0, len(se))
