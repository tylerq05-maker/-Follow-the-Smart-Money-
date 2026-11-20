"""Statistical tools. Deterministic, dependency-light, unit-tested.

Event returns are cross-sectionally correlated (many events in the same month
share market shocks), so naive iid t-stats overstate significance. All event
study inference here clusters by calendar month.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats as sps

TRADING_DAYS = 252


def clustered_mean(x: np.ndarray, clusters: np.ndarray) -> dict:
    """Mean with cluster-robust (CR1) standard error; t-dist with G-1 df."""
    x = np.asarray(x, float)
    ok = np.isfinite(x)
    x, clusters = x[ok], np.asarray(clusters)[ok]
    n = len(x)
    if n < 2:
        return {"n": int(n), "mean": float(x.mean()) if n else None, "se": None, "t": None, "p": None, "g": 0}
    m = x.mean()
    _, inv = np.unique(clusters, return_inverse=True)
    g = inv.max() + 1
    s = np.bincount(inv, weights=x - m)
    if g >= 10:
        var = (g / (g - 1)) * (s ** 2).sum() / n ** 2
        df = g - 1
    else:  # too few clusters for CR1: fall back to iid
        var = x.var(ddof=1) / n
        df = n - 1
    se = float(np.sqrt(var))
    t = float(m / se) if se > 0 else None
    p = float(2 * sps.t.sf(abs(t), df)) if t is not None else None
    return {"n": int(n), "mean": float(m), "se": se, "t": t, "p": p, "g": int(g)}


def cluster_bootstrap_ci(x: np.ndarray, clusters: np.ndarray, reps: int = 2000, alpha: float = 0.05,
                         seed: int = 7) -> tuple[float | None, float | None]:
    x = np.asarray(x, float)
    ok = np.isfinite(x)
    x, clusters = x[ok], np.asarray(clusters)[ok]
    if len(x) < 10:
        return None, None
    _, inv = np.unique(clusters, return_inverse=True)
    g = inv.max() + 1
    sums, cnts = np.bincount(inv, weights=x), np.bincount(inv).astype(float)
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, g, size=(reps, g))
    means = sums[draws].sum(1) / cnts[draws].sum(1)
    return float(np.quantile(means, alpha / 2)), float(np.quantile(means, 1 - alpha / 2))


def winsorize(x: np.ndarray, p: float = 0.01) -> np.ndarray:
    x = np.asarray(x, float)
    ok = np.isfinite(x)
    if ok.sum() < 20:
        return x
    lo, hi = np.quantile(x[ok], [p, 1 - p])
    return np.clip(x, lo, hi)


def summarize(values: np.ndarray, clusters: np.ndarray, raw: np.ndarray | None = None) -> dict:
    """Full summary of an event-level return vector (typically excess returns)."""
    v = np.asarray(values, float)
    ok = np.isfinite(v)
    if ok.sum() == 0:
        return {"n": 0}
    cm = clustered_mean(v, clusters)
    w = clustered_mean(winsorize(v), clusters)
    lo, hi = cluster_bootstrap_ci(v, clusters)
    out = {**cm, "median": float(np.median(v[ok])), "std": float(v[ok].std(ddof=1)) if ok.sum() > 1 else None,
           "hit_rate": float((v[ok] > 0).mean()), "ci_lo": lo, "ci_hi": hi,
           "mean_winsorized": w["mean"], "t_winsorized": w["t"]}
    if raw is not None:
        r = np.asarray(raw, float)
        r = r[np.isfinite(r)]
        out.update(raw_mean=float(r.mean()) if len(r) else None, raw_median=float(np.median(r)) if len(r) else None,
                   raw_win_rate=float((r > 0).mean()) if len(r) else None)
    return out


def holm(pvals: dict[str, float | None]) -> dict[str, float | None]:
    """Holm-Bonferroni adjusted p-values (family-wise error control)."""
    items = sorted(((k, p) for k, p in pvals.items() if p is not None), key=lambda kv: kv[1])
    m, out, running = len(items), {k: None for k in pvals}, 0.0
    for i, (k, p) in enumerate(items):
        running = max(running, min(1.0, (m - i) * p))
        out[k] = running
    return out


def newey_west_ols(y: np.ndarray, X: np.ndarray, lags: int = 10) -> dict:
    """OLS with Newey-West HAC standard errors. X must include a constant column."""
    y, X = np.asarray(y, float), np.asarray(X, float)
    n, k = X.shape
    xtx_inv = np.linalg.inv(X.T @ X)
    b = xtx_inv @ X.T @ y
    e = y - X @ b
    u = X * e[:, None]
    S = u.T @ u
    for L in range(1, lags + 1):
        w = 1 - L / (lags + 1)
        G = u[L:].T @ u[:-L]
        S += w * (G + G.T)
    V = xtx_inv @ S @ xtx_inv
    se = np.sqrt(np.diag(V))
    t = b / se
    r2 = 1 - (e ** 2).sum() / ((y - y.mean()) ** 2).sum()
    p = 2 * sps.t.sf(np.abs(t), n - k)
    return {"coef": b, "se": se, "t": t, "p": p, "r2": float(r2), "n": n}


def perf_stats(r: pd.Series, rf: pd.Series | None = None, bench: pd.Series | None = None) -> dict:
    """Annualised performance statistics of a daily return series."""
    r = r.dropna()
    if len(r) < 20:
        return {"n_days": int(len(r))}
    rf = (rf.reindex(r.index).fillna(0.0) if rf is not None else pd.Series(0.0, index=r.index))
    eq = (1 + r).cumprod()
    years = len(r) / TRADING_DAYS
    cagr = eq.iloc[-1] ** (1 / years) - 1
    vol = r.std() * np.sqrt(TRADING_DAYS)
    ex = r - rf
    sharpe = ex.mean() / ex.std() * np.sqrt(TRADING_DAYS) if ex.std() > 0 else None
    dd = eq / eq.cummax() - 1
    out = {"n_days": int(len(r)), "cagr": float(cagr), "vol": float(vol), "sharpe": float(sharpe) if sharpe else None,
           "max_drawdown": float(dd.min()), "total_return": float(eq.iloc[-1] - 1),
           "start": str(r.index[0].date()), "end": str(r.index[-1].date())}
    if bench is not None:
        b = bench.reindex(r.index)
        a = (r - b).dropna()
        te = a.std() * np.sqrt(TRADING_DAYS)
        out.update(active_return=float(a.mean() * TRADING_DAYS), tracking_error=float(te),
                   information_ratio=float(a.mean() * TRADING_DAYS / te) if te > 0 else None)
    return out
