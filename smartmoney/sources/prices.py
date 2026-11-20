"""Daily equity prices (Yahoo Finance chart API) with corporate-action handling.

Stored per symbol in data/raw/prices/<SYM>.parquet with:
    close      split-adjusted close (Yahoo 'close')
    adjclose   split- AND dividend-adjusted close -> total-return series
    raw_close  close as actually printed that day (split adjustment undone)
    volume     split-adjusted volume; close*volume == raw dollar volume
Returns are always computed from adjclose (dividends reinvested); the
investability price filter uses raw_close so that a stock that later split 20:1
is not mistaken for a penny stock in the past.

Known limitation: Yahoo does not serve delisted securities, so events in
companies that were later acquired or went bankrupt have no price data. This
survivorship gap is measured and reported, not hidden (see insider_study).
"""
from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from .. import config, http

log = logging.getLogger(__name__)
PRICE_DIR = config.RAW / "prices"
META_PATH = config.PROCESSED / "price_meta.parquet"
CHART = "https://query2.finance.yahoo.com/v8/finance/chart/{sym}"


def fetch_yahoo(sym: str) -> tuple[pd.DataFrame | None, dict]:
    r = http.get(CHART.format(sym=sym), ok_404=True, timeout=60, params=dict(
        period1=0, period2=int(time.time()), interval="1d", events="div,split", includeAdjustedClose="true"))
    if r is None:
        return None, {"symbol": sym, "status": "not_found"}
    res = (r.json().get("chart") or {}).get("result")
    if not res:
        return None, {"symbol": sym, "status": "empty"}
    d = res[0]
    m = d.get("meta", {})
    meta = {"symbol": sym, "status": "ok", "currency": m.get("currency"), "exchange": m.get("exchangeName"),
            "instrument_type": m.get("instrumentType"), "long_name": m.get("longName") or m.get("shortName"),
            "first_trade": m.get("firstTradeDate"), "regular_market_price": m.get("regularMarketPrice")}
    ts = d.get("timestamp")
    if not ts:
        meta["status"] = "no_history"
        return None, meta
    q = d["indicators"]["quote"][0]
    adj = d["indicators"].get("adjclose", [{}])[0].get("adjclose") or q["close"]
    off = m.get("gmtoffset", 0) or 0
    df = pd.DataFrame({
        "date": pd.to_datetime(np.asarray(ts, dtype="int64") + off, unit="s").normalize(),
        "open": q.get("open"), "close": q["close"], "adjclose": adj, "volume": q.get("volume"),
    }).dropna(subset=["close", "adjclose"])
    df = df[df["close"] > 0].drop_duplicates("date", keep="last").sort_values("date").reset_index(drop=True)
    splits = (d.get("events") or {}).get("splits") or {}
    df["raw_close"] = df["close"] * split_factor_after(df["date"], splits, off)
    df, n_bad = remove_bad_ticks(df)
    meta.update(n_obs=len(df), first_date=str(df["date"].min().date()) if len(df) else None,
                last_date=str(df["date"].max().date()) if len(df) else None, n_bad_ticks=n_bad,
                n_splits=len(splits), splits=json.dumps([
                    {"date": str(pd.to_datetime(int(s["date"]) + off, unit="s").date()), "ratio": s.get("splitRatio")}
                    for s in splits.values()]))
    return df, meta


def split_factor_after(dates: pd.Series, splits: dict, off: int = 0) -> np.ndarray:
    """Cumulative split ratio of all splits strictly AFTER each date.

    raw_close = split_adjusted_close * factor. A 10:1 split on 2024-06-10 gives
    factor 10 for every date before 2024-06-10 and 1 from that date on.
    """
    f = np.ones(len(dates))
    dv = dates.values
    for s in splits.values():
        num, den = float(s.get("numerator") or 1), float(s.get("denominator") or 1)
        if num <= 0 or den <= 0:
            continue
        sd = pd.to_datetime(int(s["date"]) + off, unit="s").normalize().to_datetime64()
        f[dv < sd] *= num / den
    return f


def remove_bad_ticks(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Drop isolated one-day spikes that fully reverse (data errors, not prices).

    A day is a bad tick if it moves >75% vs BOTH neighbours in the same
    direction and the neighbours agree with each other within 25%.
    """
    if len(df) < 3:
        return df, 0
    p = df["adjclose"].to_numpy()
    prev, nxt = np.roll(p, 1), np.roll(p, -1)
    up = (p / prev > 1.75) & (p / nxt > 1.75)
    dn = (p / prev < 1 / 1.75) & (p / nxt < 1 / 1.75)
    agree = np.abs(nxt / prev - 1) < 0.25
    bad = (up | dn) & agree
    bad[[0, -1]] = False
    return df.loc[~bad].reset_index(drop=True), int(bad.sum())


def path(sym: str) -> Path:
    return PRICE_DIR / f"{sym}.parquet"


def get_prices(sym: str, refresh: bool = False, max_age_days: float | None = None) -> pd.DataFrame | None:
    p = path(sym)
    if p.exists() and not refresh:
        if max_age_days is None or (time.time() - p.stat().st_mtime) < max_age_days * 86400:
            return pd.read_parquet(p)
    df, meta = fetch_yahoo(sym)
    if df is None:
        return None
    PRICE_DIR.mkdir(parents=True, exist_ok=True)
    df.to_parquet(p, index=False)
    return df


def _fetch_store(sym: str) -> dict:
    p = path(sym)
    if p.exists():
        df = pd.read_parquet(p)
        return {"symbol": sym, "status": "ok", "n_obs": len(df), "cached": True}
    try:
        df, meta = fetch_yahoo(sym)
    except Exception as e:  # network failure after retries: record, never fabricate
        return {"symbol": sym, "status": f"error: {e}"[:200]}
    if df is not None:
        PRICE_DIR.mkdir(parents=True, exist_ok=True)
        df.to_parquet(p, index=False)
    return meta


def build_universe_prices(workers: int = 6, symbols: list[str] | None = None) -> pd.DataFrame:
    """Fetch prices for every resolved issuer symbol plus benchmarks."""
    from . import sec_reference
    if symbols is None:
        ref = sec_reference.load_reference()
        symbols = sorted(set(ref["yahoo_symbol"].dropna()))
    symbols = sorted(set(symbols) | {config.BENCHMARK, config.SIZE_BENCHMARK,
                                     "XLB", "XLE", "XLI", "XLP", "XLY", "XLV", "XLK", "XLC", "XLU", "XLF", "XLRE"})
    metas = []
    with ThreadPoolExecutor(workers) as ex:
        for i, m in enumerate(ex.map(_fetch_store, symbols)):
            metas.append(m)
            if i % 500 == 0:
                log.info("prices %d/%d", i, len(symbols))
    meta = pd.DataFrame(metas)
    if META_PATH.exists():
        old = pd.read_parquet(META_PATH)
        meta = pd.concat([old[~old.symbol.isin(meta[~meta.get("cached", False).fillna(False).astype(bool)].symbol)],
                          meta[~meta.get("cached", False).fillna(False).astype(bool)]], ignore_index=True)
    meta.to_parquet(META_PATH, index=False)
    log.info("prices: %s", meta["status"].value_counts().head(5).to_dict())
    return meta
