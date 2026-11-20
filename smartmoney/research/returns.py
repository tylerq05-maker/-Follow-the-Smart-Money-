"""Forward returns with a strict, conservative timing convention.

    filing date F  (EDGAR filing date; Section 16 filings accepted up to 10pm ET
                    keep date F, so information may arrive AFTER F's close)
    signal day  s  = last trading day <= F   (features may use prices up to s)
    entry day   e  = first trading day  > F  (position opened at e's CLOSE)
    exit        e+h trading days later, at the close

Returns use total-return (dividend-adjusted) closes. Benchmarks are measured
over exactly the same calendar dates. If a series ends before e+h and before
the end of the data (a delisting), the return to the last available price is
used and flagged `truncated`; if the horizon simply has not elapsed yet the
return is missing (not matured).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .. import config
from ..sources import prices

PATH_DAYS = np.array([0, 1, 2, 3, 4, 5, 7, 10, 15, 20, 25, 30, 40, 50, 60, 70, 80, 90, 105, 120, 135, 150,
                      165, 180, 200, 220, 252])


@dataclass
class Series:
    dates: np.ndarray  # datetime64[D]
    adj: np.ndarray
    raw: np.ndarray
    dvol: np.ndarray

    @classmethod
    def load(cls, sym: str) -> "Series | None":
        p = prices.path(sym)
        if not p.exists():
            return None
        df = pd.read_parquet(p, columns=["date", "close", "adjclose", "raw_close", "volume"])
        if len(df) < 2:
            return None
        return cls(df["date"].values.astype("datetime64[D]"), df["adjclose"].to_numpy(float),
                   df["raw_close"].to_numpy(float), (df["close"] * df["volume"].fillna(0)).to_numpy(float))

    def at_or_before(self, d: np.ndarray) -> np.ndarray:
        return np.searchsorted(self.dates, d, side="right") - 1


def bench_return(b: Series, start: np.ndarray, end: np.ndarray) -> np.ndarray:
    i, j = b.at_or_before(start), b.at_or_before(end)
    out = np.full(len(start), np.nan)
    ok = (i >= 0) & (j >= 0)
    out[ok] = b.adj[j[ok]] / b.adj[i[ok]] - 1
    return out


def event_returns(s: Series, filing_dates: np.ndarray, spy: Series, iwm: Series, data_end: np.datetime64,
                  horizons=config.HORIZONS) -> dict[str, np.ndarray]:
    """Market features and forward returns for events of one symbol."""
    n, m = len(s.dates), len(filing_dates)
    fd = filing_dates.astype("datetime64[D]")
    i_sig = s.at_or_before(fd)
    i0 = i_sig + 1
    ok = (i_sig >= 0) & (i0 < n)
    ok[ok] &= (s.dates[i0[ok]] - fd[ok]) <= np.timedelta64(7, "D")      # trading resumed promptly
    ok[ok] &= (fd[ok] - s.dates[i_sig[ok]]) <= np.timedelta64(7, "D")   # stock was trading at the filing
    o = {k: np.full(m, np.nan) for k in ["raw_close_sig", "med_dvol20", "prior_ret_5d", "prior_ret_20d", "prior_ret_60d",
                                         "prior_ret_250d", "beta"]}
    o["has_price"] = ok
    o["sig_date"] = np.full(m, np.datetime64("NaT", "D"), "datetime64[D]")
    o["entry_date"] = np.full(m, np.datetime64("NaT", "D"), "datetime64[D]")
    idx = np.where(ok)[0]
    if len(idx) == 0:
        for h in horizons:
            for k in ("ret", "spy", "iwm"):
                o[f"{k}_{h}"] = np.full(m, np.nan)
            o[f"trunc_{h}"] = np.zeros(m, bool)
        return o
    sg, e0 = i_sig[idx], i0[idx]
    o["sig_date"][idx], o["entry_date"][idx] = s.dates[sg], s.dates[e0]
    o["raw_close_sig"][idx] = s.raw[sg]
    o["med_dvol20"][idx] = [np.median(s.dvol[max(0, k - 19):k + 1]) if k >= 9 else np.nan for k in sg]
    for lb in (5, 20, 60, 250):
        v = np.full(len(idx), np.nan)
        hit = sg >= lb
        v[hit] = s.adj[sg[hit]] / s.adj[sg[hit] - lb] - 1
        o[f"prior_ret_{lb}d"][idx] = v
    # Market beta over [s-252, s-21] trading days (needs >= 120 overlapping obs).
    sr = np.diff(np.log(s.adj))
    spy_idx = spy.at_or_before(s.dates)
    spy_lr = np.full(n, np.nan)
    good = spy_idx >= 1
    spy_lr[good] = np.log(spy.adj[spy_idx[good]] / spy.adj[spy_idx[good] - 1])
    for j, k in enumerate(sg):
        a, b = max(1, k - 252), k - 21
        if b - a >= 120:
            x, y = spy_lr[a:b], sr[a - 1:b - 1]
            f = np.isfinite(x) & np.isfinite(y)
            if f.sum() >= 120 and x[f].var() > 0:
                o["beta"][idx[j]] = np.cov(x[f], y[f])[0, 1] / x[f].var(ddof=1)
    ended = s.dates[-1] < data_end - np.timedelta64(10, "D")
    entry_d = s.dates[e0]
    for h in horizons:
        j = e0 + h
        ret, trunc = np.full(m, np.nan), np.zeros(m, bool)
        exit_d = np.full(m, np.datetime64("NaT", "D"), "datetime64[D]")
        inr = j < n
        r = np.full(len(idx), np.nan)
        ex = np.full(len(idx), np.datetime64("NaT", "D"), "datetime64[D]")
        r[inr] = s.adj[j[inr]] / s.adj[e0[inr]] - 1
        ex[inr] = s.dates[j[inr]]
        if ended:  # delisted / stopped trading: hold to last price
            r[~inr] = s.adj[-1] / s.adj[e0[~inr]] - 1
            ex[~inr] = s.dates[-1]
            trunc[idx[~inr]] = True
        ret[idx], exit_d[idx] = r, ex
        o[f"ret_{h}"], o[f"trunc_{h}"] = ret, trunc
        has = ~np.isnat(exit_d)
        for name, b in (("spy", spy), ("iwm", iwm)):
            v = np.full(m, np.nan)
            v[has] = bench_return(b, o["entry_date"][has], exit_d[has])
            o[f"{name}_{h}"] = v
    # Look-ahead diagnostic: the return a naive backtest would book by buying at the
    # filing-date close (only when the filing date is itself a trading day).
    naive = np.full(m, np.nan)
    same = s.dates[sg] == fd[idx]
    k = np.minimum(sg + 1, n - 1)
    naive_vals = s.adj[k] / s.adj[sg] - 1
    naive[idx[same]] = naive_vals[same]
    o["naive_ret_1"] = naive
    sp = np.full(m, np.nan)
    sp[idx[same]] = bench_return(spy, s.dates[sg][same], s.dates[k][same])
    o["naive_spy_1"] = sp
    return o


def event_paths(s: Series, entry_idx: np.ndarray, spy: Series) -> np.ndarray:
    """Cumulative excess-over-SPY return at PATH_DAYS after entry (NaN when unavailable)."""
    n = len(s.dates)
    j = entry_idx[:, None] + PATH_DAYS[None, :]
    valid = j < n
    jj = np.where(valid, j, n - 1)
    r = s.adj[jj] / s.adj[entry_idx][:, None] - 1
    b = bench_return(spy, np.repeat(s.dates[entry_idx], len(PATH_DAYS)), s.dates[jj].ravel()).reshape(j.shape)
    out = r - b
    out[~valid] = np.nan
    return out.astype("float32")
