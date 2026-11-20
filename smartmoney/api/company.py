"""Per-company Smart Money Radar data (insider section in Phase 1).

History comes from the SEC bulk datasets; filings newer than the last bulk
quarter are fetched live from EDGAR and parsed with the same schema. Signals
are re-evaluated with exactly the same code used in the backtest.
"""
from __future__ import annotations

import logging
import time
from functools import lru_cache

import duckdb
import numpy as np
import pandas as pd

from .. import config
from ..insiders import signals as sg
from ..insiders.classify import TX_LABELS, classify
from ..research import returns as R
from ..sources import prices, sec_form4_xml, sec_insider_bulk, sec_reference

log = logging.getLogger(__name__)
_TTL: dict[str, tuple[float, object]] = {}


def _cached(key: str, ttl: float, fn):
    hit = _TTL.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    val = fn()
    _TTL[key] = (time.time(), val)
    return val


@lru_cache(maxsize=1)
def ticker_table() -> pd.DataFrame:
    return sec_reference.sec_ticker_table()


def search(q: str, limit: int = 10) -> list[dict]:
    t = ticker_table()
    q = q.strip().upper()
    if not q:
        return []
    exact = t[t.ticker == q]
    pref = t[t.ticker.str.startswith(q) & (t.ticker != q)]
    name = t[t.name.str.upper().str.contains(q, regex=False) & ~t.ticker.str.startswith(q)]
    out = pd.concat([exact, pref.sort_values("ticker", key=lambda s: s.str.len()), name]).head(limit)
    return [{"ticker": r.ticker, "name": r.name, "cik": int(r.cik), "exchange": r.exchange} for r in out.itertuples()]


def resolve(ticker: str) -> dict | None:
    t = ticker_table()
    hit = t[t.ticker == ticker.upper().replace("-", "-")]
    if hit.empty:
        hit = t[t.ticker == ticker.upper().replace("-", ".")]
    if hit.empty:
        return None
    r = hit.iloc[0]
    return {"ticker": r.ticker, "cik": int(r.cik), "name": r["name"], "exchange": r.exchange}


def _bulk_transactions(cik: int) -> pd.DataFrame:
    files = str(sec_insider_bulk.OUT_DIR / "*.parquet")
    con = duckdb.connect()
    df = con.execute(f"SELECT * FROM read_parquet('{files}') WHERE issuer_cik = ?", [cik]).df()
    con.close()
    for c in ("filing_date", "trans_date", "period_of_report"):
        df[c] = pd.to_datetime(df[c])
    return df


def transactions(cik: int) -> tuple[pd.DataFrame, dict]:
    bulk = _bulk_transactions(cik)
    last_bulk = bulk["filing_date"].max() if len(bulk) else pd.Timestamp("2009-01-01")
    live, live_err = pd.DataFrame(), None
    try:
        live = sec_form4_xml.live_transactions(cik, since=last_bulk + pd.Timedelta(days=1))
    except Exception as e:  # EDGAR unavailable: serve history, say so
        live_err = str(e)[:200]
    if len(live):
        live["source"] = "edgar_live"
        live["trans_sk"] = np.arange(len(live))
    bulk["source"] = "sec_bulk"
    tx = pd.concat([bulk, live], ignore_index=True) if len(live) else bulk
    if tx.empty:
        return tx, {"last_bulk_filing": None, "live_filings": 0, "live_error": live_err}
    tx = classify(tx)
    return tx, {"last_bulk_filing": str(last_bulk.date()) if len(bulk) else None,
                "live_filings": int(live["accession"].nunique()) if len(live) else 0, "live_error": live_err}


def _num(x):
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return None
    if isinstance(x, (np.floating, float)):
        return float(round(x, 6))
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (pd.Timestamp, np.datetime64)):
        return str(pd.Timestamp(x).date()) if not pd.isna(x) else None
    if isinstance(x, (np.bool_,)):
        return bool(x)
    return x


def _records(df: pd.DataFrame) -> list[dict]:
    return [{k: _num(v) for k, v in r.items()} for r in df.to_dict("records")]


def price_block(sym: str) -> dict:
    df = prices.get_prices(sym, max_age_days=0.5)
    if df is None or df.empty:
        return {"available": False}
    last = df.iloc[-1]
    adj = df.set_index("date")["adjclose"]

    def ret(days):
        if len(adj) <= days:
            return None
        return float(adj.iloc[-1] / adj.iloc[-1 - days] - 1)

    ytd = adj[adj.index >= pd.Timestamp(year=adj.index[-1].year, month=1, day=1)]
    hist = df[df.date >= df.date.iloc[-1] - pd.Timedelta(days=3 * 365)]
    return {"available": True, "last_date": str(last.date.date()), "last_price": float(last.raw_close),
            "returns": {"1m": ret(21), "3m": ret(63), "6m": ret(126), "1y": ret(252),
                        "ytd": float(ytd.iloc[-1] / ytd.iloc[0] - 1) if len(ytd) > 1 else None},
            "high_52w": float(df.raw_close.tail(252).max()), "low_52w": float(df.raw_close.tail(252).min()),
            "vol_1y": float(np.log(adj).diff().tail(252).std() * np.sqrt(252)),
            "avg_dollar_volume_20d": float((df.close * df.volume).tail(20).mean()),
            "series": {"dates": hist.date.dt.strftime("%Y-%m-%d").tolist(),
                       "close": [round(float(x), 4) for x in hist.raw_close]}}


def company(ticker: str) -> dict | None:
    ident = resolve(ticker)
    if ident is None:
        return None
    return _cached(f"co:{ident['ticker']}", 900, lambda: _company(ident))


def _company(ident: dict) -> dict:
    cik, sym = ident["cik"], sec_reference.yahoo_symbol(ident["ticker"])
    overview = dict(ident)
    try:
        sub = sec_reference.submissions(cik)
        sic = int(sub.get("sic") or 0) or None
        sector, etf = sec_reference.sic_to_sector(sic)
        overview.update(sic=sic, sic_description=sub.get("sicDescription"), sector=sector, sector_etf=etf,
                        state=sub.get("stateOfIncorporation"), fiscal_year_end=sub.get("fiscalYearEnd"),
                        website=sub.get("website") or None)
    except Exception as e:
        overview["sec_error"] = str(e)[:200]
    px = price_block(sym)
    try:
        shs, shs_date = sec_reference.shares_outstanding(cik)
    except Exception:
        shs, shs_date = None, None
    overview.update(shares_outstanding=shs, shares_outstanding_date=shs_date,
                    market_cap=(shs * px["last_price"]) if shs and px.get("available") else None)

    tx, feed = transactions(cik)
    try:
        inst = institution_block(ident["ticker"])
    except Exception as e:  # never let one source break the page
        log.exception("institutions %s", ident["ticker"])
        inst = {"available": False, "reason": str(e)[:200]}
    try:
        crowd = crowd_block(ident["ticker"])
    except Exception as e:
        log.exception("crowd %s", ident["ticker"])
        crowd = {"available": False, "reason": str(e)[:200]}
    return {"overview": overview, "price": px, "insiders": insider_block(tx, sym, feed), "institutions": inst, "crowd": crowd,
            "sources": {"sec_filings": f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=4",
                        "prices": "Yahoo Finance daily chart API (split & dividend adjusted)"}}


def insider_block(tx: pd.DataFrame, sym: str, feed: dict) -> dict:
    if tx.empty:
        return {"available": False, "feed": feed}
    tx = tx.sort_values(["filing_date", "trans_date"], ascending=False)
    now = pd.Timestamp.now().normalize()
    om = tx[tx.trans_code.isin(["P", "S"])]

    def window(days):
        w = om[om.filing_date >= now - pd.Timedelta(days=days)]
        b, s = w[w.quality == "discretionary_buy"], w[w.quality == "discretionary_sell"]
        p = w[w.quality == "planned_sell"]
        return {"buy_value": float(b.value.sum()), "buyers": int(b[b.is_insider].owner_cik.nunique()),
                "discretionary_sell_value": float(s.value.sum()), "sellers": int(s[s.is_insider].owner_cik.nunique()),
                "planned_sell_value": float(p.value.sum()), "n_buy_filings": int(b.accession.nunique()),
                "n_sell_filings": int(s.accession.nunique())}

    recent = tx.head(200).copy()
    recent["accession_url"] = [f"https://www.sec.gov/Archives/edgar/data/{int(c)}/{a.replace('-', '')}/"
                               for c, a in zip(recent.issuer_cik.fillna(0), recent.accession)]
    cols = ["filing_date", "trans_date", "owner_name", "officer_title", "role", "trans_code", "tx_label", "shares",
            "price", "value", "shares_after", "holding_change", "quality", "exclusion_reason", "planned",
            "filing_lag_bdays", "late_filing", "doc_type", "accession", "accession_url", "source"]
    recent = recent[[c for c in cols if c in recent.columns]].replace([np.inf, -np.inf], None)

    # Monthly net discretionary flow for the activity chart (last 5 years).
    m = om[(om.filing_date >= now - pd.DateOffset(years=5)) & om.quality.isin(
        ["discretionary_buy", "discretionary_sell", "planned_sell"])].copy()
    m["month"] = m.filing_date.dt.to_period("M").astype(str)
    flow = m.pivot_table(index="month", columns="quality", values="value", aggfunc="sum", fill_value=0)
    flow = flow.reindex(columns=["discretionary_buy", "discretionary_sell", "planned_sell"], fill_value=0)

    holders = (om[om.is_insider].groupby(["owner_cik"]).agg(
        name=("owner_name", "first"), role=("role", "first"), title=("officer_title", "first"),
        last_filing=("filing_date", "max"),
        bought=("value", lambda v: float(v[om.loc[v.index, "quality"] == "discretionary_buy"].sum())),
        sold=("value", lambda v: float(v[om.loc[v.index, "quality"].isin(["discretionary_sell", "planned_sell"])].sum())))
        .sort_values("last_filing", ascending=False).head(15).reset_index())

    signals = live_signals(tx, sym)
    counts = tx.groupby("tx_label").size().sort_values(ascending=False)
    return {"available": True, "feed": feed, "windows": {"90d": window(90), "180d": window(180), "365d": window(365)},
            "recent": _records(recent), "flow": {"months": flow.index.tolist(),
                                                  **{c: [float(x) for x in flow[c]] for c in flow.columns}},
            "insiders": _records(holders), "signals": signals,
            "code_mix": [{"label": k, "n": int(v)} for k, v in counts.items()],
            "n_transactions": int(len(tx)), "first_filing": str(tx.filing_date.min().date())}


def live_signals(tx: pd.DataFrame, sym: str) -> list[dict]:
    """Evaluate the backtested signal rules on this issuer's full filing history."""
    t = tx[tx.trans_code.isin(["P", "S"]) & tx.issuer_cik.notna()].copy()
    t["issuer_cik"] = t["issuer_cik"].astype("int64")
    t = t[t.quality != "excluded"]
    if t.empty:
        return []
    t["owner_cik"] = t["owner_cik"].fillna(-1).astype("int64")
    t["trans_sk"] = np.arange(len(t))
    ev = sg.build_events(t)
    s = R.Series.load(sym)
    spy, iwm = R.Series.load(config.BENCHMARK), R.Series.load(config.SIZE_BENCHMARK)
    if s is None or spy is None:
        return []
    o = R.event_returns(s, ev["filing_date"].values, spy, iwm, spy.dates[-1])
    ev = ev.join(pd.DataFrame(o, index=ev.index))
    for h in config.HORIZONS:
        ev[f"exc_{h}"] = ev[f"ret_{h}"] - ev[f"spy_{h}"]
    sigs = sg.signal_events(ev)
    if sigs.empty:
        return []
    sigs = sigs.sort_values("filing_date", ascending=False).head(60)
    cols = ["signal", "direction", "filing_date", "entry_date", "n_insiders", "n_insiders_30d", "value",
            "ceo_cfo_value", "max_holding_change", "prior_ret_60d", "n_opportunistic", "any_first_in_2y",
            "exc_5", "exc_20", "exc_60", "exc_180", "ret_60", "raw_close_sig", "med_dvol20"]
    out = sigs[cols].replace([np.inf, -np.inf], None)
    out["signal_name"] = out.signal.map(lambda k: sg.SIGNAL_BY_KEY[k].name)
    out["investable"] = (out.raw_close_sig >= config.MIN_PRICE) & (out.med_dvol20 >= config.MIN_MEDIAN_DOLLAR_VOLUME)
    return _records(out)


# ------------------------------------------------------------------ institutions (Phase 2)
@lru_cache(maxsize=1)
def _f13():
    from ..institutions import build as B
    from ..research import institution_study as IS
    if not B.STOCK_PATH.exists():
        return None
    return {"map": pd.read_parquet(B.MAP_PATH), "stock": pd.read_parquet(B.STOCK_PATH),
            "recent": pd.read_parquet(B.RECENT_PATH),
            "signals": pd.read_parquet(IS.SIGNAL_EVENTS_PATH) if IS.SIGNAL_EVENTS_PATH.exists() else pd.DataFrame(),
            "names": {s.key: s.name for s in IS.SIGNALS}}


def institution_block(ticker: str) -> dict:
    d = _f13()
    if d is None:
        return {"available": False, "reason": "13F data not built"}
    cus = d["map"].loc[d["map"].ticker == ticker.upper(), "cusip"].tolist()
    if not cus:
        return {"available": False, "reason": "No verified 13F CUSIP for this ticker"}
    st = d["stock"][d["stock"].cusip.isin(cus)].groupby("period").agg(
        n_holders=("n_holders", "sum"), inst_value=("inst_value", "sum"), breadth_change=("breadth_change", "sum"),
        c_holders=("c_holders", "sum"), c_new=("c_new", "sum"), c_incr=("c_incr", "sum"), c_decr=("c_decr", "sum"),
        c_exit=("c_exit", "sum"), n_new=("n_new", "sum"), n_exit=("n_exit", "sum"),
        signal_date=("signal_date", "max")).reset_index().sort_values("period")
    rec = d["recent"][d["recent"].cusip.isin(cus)]
    out = {"available": True, "cusips": cus, "history": _records(st.tail(24))}
    if len(rec):
        periods = sorted(rec.period.unique())
        last = periods[-1]
        cur = rec[rec.period == last].groupby(["filer_cik", "manager"]).agg(
            shares=("shares", "sum"), value=("value_usd", "sum"), aum=("aum", "first"), filing_date=("filing_date", "min"),
            concentrated=("concentrated", "first"), notable=("notable", "first")).reset_index()
        if len(periods) > 1:
            prv = rec[rec.period == periods[-2]].groupby("filer_cik")["shares"].sum().rename("prev_shares")
            cur = cur.merge(prv, on="filer_cik", how="left")
            exits = rec[(rec.period == periods[-2]) & ~rec.filer_cik.isin(cur.filer_cik)].groupby(["filer_cik", "manager"]).agg(
                prev_shares=("shares", "sum"), concentrated=("concentrated", "first"), notable=("notable", "first")).reset_index()
        else:
            cur["prev_shares"], exits = np.nan, pd.DataFrame()
        cur["change"] = cur.shares / cur.prev_shares - 1
        cur["status"] = np.where(cur.prev_shares.isna(), "new", np.where(cur.change >= 0.25, "increased",
                                 np.where(cur.change <= -0.25, "reduced", "held")))
        cur["weight"] = cur.value / cur.aum
        cur = cur.sort_values("value", ascending=False)
        out.update(period=str(pd.Timestamp(last).date()), last_filing=str(cur.filing_date.max().date()),
                   n_holders=int(len(cur)), total_value=float(cur.value.sum()),
                   top=_records(cur.head(20).drop(columns=["aum"])),
                   notable=_records(pd.concat([cur[cur.notable], exits[exits.notable].assign(status="exited") if len(exits) else None])
                                    .drop(columns=["aum"], errors="ignore")),
                   concentrated_moves=_records(pd.concat([cur[cur.concentrated & (cur.status != "held")],
                                                          exits[exits.concentrated].assign(status="exited") if len(exits) else None])
                                               .sort_values("value", ascending=False, na_position="last").head(25).drop(columns=["aum"], errors="ignore")),
                   n_exits=int(len(exits)), status_counts=cur.status.value_counts().to_dict())
    sg_ = d["signals"]
    if len(sg_):
        s = sg_[sg_.cusip.isin(cus)].sort_values("filing_date", ascending=False).head(40)
        s = s.assign(signal_name=s.signal.map(d["names"]))
        out["signals"] = _records(s.reindex(columns=["signal", "signal_name", "direction", "period", "filing_date", "manager",
                                                    "weight", "c_buyers", "c_sellers", "breadth_change", "exc_20", "exc_60", "exc_180"]))
    return out


# ------------------------------------------------------------------ crowd attention (Phase 3)
def crowd_block(ticker: str) -> dict:
    from ..research import crowd_study as CS
    from ..sources import wikipedia
    m = wikipedia.ticker_articles()
    hit = m[m.ticker == ticker.upper()]
    if hit.empty:
        return {"available": False, "reason": "No unambiguous English Wikipedia article linked to this ticker in Wikidata"}
    v = wikipedia.load(ticker.upper())
    if v is None:
        return {"available": False, "reason": "Page views not downloaded yet"}
    s = v.set_index("date")["views"].asfreq("D").fillna(0)
    base = s.shift(1).rolling(CS.BASE_DAYS, min_periods=40).median()
    ratio = s / base.replace(0, np.nan)
    w = pd.DataFrame({"views": s, "baseline": base, "ratio": ratio}).last("730D")
    spikes = w[(w.ratio >= CS.SPIKE_RATIO) & (w.views >= CS.SPIKE_MIN_VIEWS)]
    last7 = s.tail(7).mean()
    out = {"available": True, "article": hit.iloc[0].title, "last_day": str(s.index[-1].date()),
           "last_views": float(s.iloc[-1]), "avg_7d": float(last7), "baseline_60d": float(base.iloc[-1]),
           "ratio_7d": float(last7 / base.iloc[-1]) if base.iloc[-1] else None,
           "series": {"dates": [d.strftime("%Y-%m-%d") for d in w.index], "views": [float(x) for x in w.views],
                      "baseline": [None if pd.isna(x) else float(x) for x in w.baseline]},
           "spikes": _records(spikes.reset_index().rename(columns={"index": "date"}).tail(30))}
    if CS.SIGNAL_EVENTS_PATH.exists():
        se = pd.read_parquet(CS.SIGNAL_EVENTS_PATH, filters=[("ticker", "==", ticker.upper())])
        names = {x.key: x.name for x in CS.SIGNALS}
        se = se.sort_values("filing_date", ascending=False).head(30).assign(signal_name=lambda d: d.signal.map(names))
        out["signals"] = _records(se.reindex(columns=["signal", "signal_name", "direction", "day", "filing_date", "views",
                                                      "ratio", "prior_ret_5d", "exc_5", "exc_20", "exc_60"]))
    return out
