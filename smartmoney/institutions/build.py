"""13F analytics: identity mapping, manager classification, position changes, point-in-time events.

Timing rules (verified in tests/test_institutions.py):
  * A manager's holdings for quarter P are public on THAT FILING's filing date.
  * Quarter-wide aggregates (breadth, hedge-fund crowding) are public only once every manager
    has filed: the statutory deadline, P + 45 days (rolled to the next business day). Only
    original reports filed by that deadline are aggregated; late filers and amendments are excluded.
  * Changes compare P with P-1 for managers that filed both, with P-1 shares split-adjusted.
"""
from __future__ import annotations

import json
import logging
import re

import duckdb
import numpy as np
import pandas as pd

from .. import config, http
from ..sources import prices, sec_13f, sec_reference

log = logging.getLogger(__name__)
P = config.PROCESSED
MAP_PATH = P / "f13_cusip_map.parquet"
FILINGS_PATH = P / "f13_filings.parquet"
MGR_PATH = P / "f13_manager_period.parquet"
STOCK_PATH = P / "f13_stock_period.parquet"
MGR_EVENTS_PATH = P / "f13_manager_events.parquet"
RECENT_PATH = P / "f13_recent_holdings.parquet"
FIGI_CACHE = config.CACHE / "openfigi.json"

CONC_MAX_POSITIONS = 150
CONC_MIN_TOP10 = 0.50
CONC_MIN_AUM = 1e8
CONC_MIN_POSITIONS = 10
CONC_MAX_ETF_SHARE = 0.20
ETF_PATTERN = ("ETF|ISHARES|SPDR|VANGUARD|INVESCO QQQ|SELECT SECTOR|INDEX FD|TRUST UNIT|PROSHARES|WISDOMTREE|"
               "SCHWAB STRATEGIC|J P MORGAN EXCHANGE|DIMENSIONAL ETF")
BIG_CHANGE = 0.25
MIN_ISSUER_HOLDERS = 10  # CUSIPs ever held by >= this many managers are mapped

# Well-known discretionary / activist managers (display + one signal). Matched by CIK below.
NOTABLE = {
    1067983: "Berkshire Hathaway", 1336528: "Pershing Square", 1656456: "Appaloosa", 1040273: "Third Point",
    1061768: "Baupost Group", 1079114: "Greenlight Capital", 921669: "Icahn Capital", 1167483: "Tiger Global",
    1135730: "Coatue", 1061165: "Lone Pine", 1103804: "Viking Global", 934639: "Maverick Capital",
    1791786: "Elliott Investment Mgmt", 1517137: "Starboard Value", 1418814: "ValueAct", 1159159: "Jana Partners",
    1345471: "Trian Fund Mgmt", 1536411: "Duquesne Family Office", 1649339: "Scion Asset Mgmt",
    1029160: "Soros Fund Mgmt", 1350694: "Bridgewater", 1037389: "Renaissance Technologies",
    1423053: "Citadel Advisors", 1009207: "D. E. Shaw", 1273087: "Millennium Mgmt", 1603466: "Point72",
    1048445: "Elliott Management", 1006438: "Appaloosa Management", 1998597: "JANA Partners Mgmt",
}


def deadline(period: pd.Series) -> pd.Series:
    d = period + pd.Timedelta(days=45)
    return pd.Series(np.busday_offset(d.values.astype("datetime64[D]"), 0, roll="forward"), index=period.index).astype("datetime64[ns]")


# ------------------------------------------------------------------ filings
def build_filings(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    f = con.execute(f"""
        SELECT accession, filer_cik, any_value(manager) manager, min(filing_date) filing_date, min(period) period,
               count(*) n_positions, sum(value_usd) aum
        FROM read_parquet('{sec_13f.glob()}') GROUP BY accession, filer_cik""").df()
    f = f[f.period.notna() & f.filing_date.notna()]
    # A handful of reports carry impossible periods (e.g. 1987, or after the filing date): data-entry errors.
    f = f[(f.period >= pd.Timestamp("2013-01-01")) & (f.period <= f.filing_date + pd.Timedelta(days=1))]
    f["period"] = f["period"] + pd.offsets.QuarterEnd(0)  # normalise odd period dates to quarter end
    f["deadline"] = deadline(f["period"])
    f["on_time"] = f["filing_date"] <= f["deadline"]
    # First original per manager-period = first moment the information was public.
    f = f.sort_values(["filer_cik", "period", "filing_date", "accession"])
    f["dup"] = f.duplicated(["filer_cik", "period"])
    f.to_parquet(FILINGS_PATH, index=False)
    log.info("13F filings: %d (dup originals %d, late %d)", len(f), f.dup.sum(), (~f.on_time).sum())
    return f


# ------------------------------------------------------------------ identity
_STRIP = re.compile(r"\b(INC|INCORPORATED|CORP|CORPORATION|CO|COMPANY|LTD|LIMITED|PLC|LLC|LP|L P|SA|NV|N V|AG|SE|"
                    r"THE|NEW|DEL|DE|HLDGS|HOLDINGS?|GROUP|GRP|COM|CL [A-Z]|CLASS [A-Z]|ORD|SHS|ADR|SPONSORED)\b")


def norm_name(s: str) -> str:
    s = re.sub(r"[^A-Z0-9 ]", " ", (s or "").upper().replace("&", " AND "))
    return re.sub(r"\s+", " ", _STRIP.sub(" ", s)).strip()


def _openfigi(cusips: list[str]) -> dict[str, str | None]:
    cache = json.loads(FIGI_CACHE.read_text()) if FIGI_CACHE.exists() else {}
    todo = [c for c in cusips if c not in cache]
    import time
    for i in range(0, len(todo), 10):
        batch = todo[i:i + 10]
        jobs = [{"idType": "ID_CUSIP", "idValue": c, "exchCode": "US"} for c in batch]
        for attempt in range(5):
            r = http._session().post("https://api.openfigi.com/v3/mapping", json=jobs, timeout=60)
            if r.status_code == 429:
                time.sleep(15 * (attempt + 1))
                continue
            break
        if r.status_code != 200:
            log.warning("openfigi %s", r.status_code)
            continue
        for c, res in zip(batch, r.json()):
            d = (res.get("data") or [{}])[0]
            cache[c] = d.get("ticker") if d.get("marketSector", "Equity") == "Equity" else None
        time.sleep(2.5)  # 25 requests/minute without an API key
        if i % 500 == 0:
            FIGI_CACHE.write_text(json.dumps(cache))
            log.info("openfigi %d/%d", i, len(todo))
    FIGI_CACHE.write_text(json.dumps(cache))
    return {c: cache.get(c) for c in cusips}


def _price_score(sym: str, obs: pd.DataFrame) -> tuple[int, float]:
    """(n periods checked, share within 10%) of implied 13F price vs market close at period end."""
    df = prices.get_prices(sym)
    if df is None or df.empty:
        return 0, 0.0
    d = df["date"].values.astype("datetime64[D]")
    t = obs["period"].values.astype("datetime64[D]")
    i = np.searchsorted(d, t, side="right") - 1
    ok = (i >= 0) & ((t - d[np.maximum(i, 0)]) <= np.timedelta64(5, "D"))
    if ok.sum() == 0:
        return 0, 0.0
    ratio = obs["implied_px"].values[ok] / df["raw_close"].values[i[ok]]
    return int(ok.sum()), float((np.abs(ratio - 1) <= 0.10).mean())


def build_cusip_map(con, use_openfigi: bool = True) -> pd.DataFrame:
    """CUSIP -> verified price symbol. Candidates from issuer-name matching against the SEC
    ticker table and OpenFIGI; a candidate is accepted only if 13F implied prices
    (value / shares) match its market close at >= 70% of quarter ends."""
    obs = con.execute(f"""
        SELECT cusip, period, any_value(issuer_name) issuer_name, any_value(title) title,
               median(value_usd / shares) implied_px, count(*) n_holders
        FROM read_parquet('{sec_13f.glob()}') GROUP BY cusip, period""").df()
    obs["period"] = obs["period"] + pd.offsets.QuarterEnd(0)
    obs = obs.sort_values("period")
    agg = obs.groupby("cusip").agg(max_holders=("n_holders", "max"), issuer_name=("issuer_name", "last"),
                                   title=("title", "last"), last_period=("period", "max"))
    agg = agg[(agg.max_holders >= MIN_ISSUER_HOLDERS)
              & ~agg.title.fillna("").str.upper().str.contains(r"PFD|PREF|NOTE|WARRANT|WT|DEBT|BOND|RIGHT|UNIT|CALL|PUT|ETF|TR UNIT")]
    sec = sec_reference.sec_ticker_table()
    sec = sec[sec["ticker"].map(lambda t: isinstance(t, str) and t != "")]
    sec["norm"] = sec["name"].map(norm_name)
    by_name = sec.groupby("norm")["ticker"].apply(list).to_dict()
    agg["norm"] = agg["issuer_name"].map(norm_name)
    agg["name_candidates"] = agg["norm"].map(lambda n: by_name.get(n, [])[:3])
    # OpenFIGI (rate-limited) only for unmatched CUSIPs that are still widely held: delisted names have no
    # free price history anyway, and renamed survivors already match on their latest issuer name.
    need = agg[(agg.name_candidates.str.len() == 0) & (agg.last_period >= pd.Timestamp("2023-01-01"))
               & (agg.max_holders >= 20)]
    log.info("name-matched %d of %d cusips; OpenFIGI lookups: %d", (agg.name_candidates.str.len() > 0).sum(), len(agg), len(need))
    figi = _openfigi(need.index.tolist()) if use_openfigi else {}
    agg["figi_ticker"] = [t if isinstance(t, str) and t else None for t in agg.index.map(lambda c: figi.get(c))]
    obs_by = obs.groupby("cusip")
    rows = []
    fetch = set()
    for c, r in agg.iterrows():
        for t in list(r.name_candidates) + ([r.figi_ticker] if r.figi_ticker else []):
            if isinstance(t, str) and t:
                fetch.add(sec_reference.yahoo_symbol(t))
    missing = [s for s in fetch if not prices.path(s).exists()]
    log.info("cusip map: %d cusips, fetching %d new price series", len(agg), len(missing))
    if missing:
        prices.build_universe_prices(workers=6, symbols=missing)
    tick2cik = sec.drop_duplicates("ticker").set_index("ticker")["cik"].to_dict()
    for c, r in agg.iterrows():
        o = obs_by.get_group(c)
        best = (None, 0, 0.0, "unmapped")
        cands = [(t, "name") for t in r.name_candidates] + [(r.figi_ticker, "openfigi")]
        cands = [(t, how) for t, how in cands if isinstance(t, str) and t]
        for t, how in cands:
            n, share = _price_score(sec_reference.yahoo_symbol(t), o)
            if n >= 2 and share >= 0.7 and (share, n) > (best[2], best[1]):
                best = (t, n, share, how)
            elif best[0] is None and n > 0:
                best = (None, n, share, f"rejected_{how}")
        rows.append({"cusip": c, "issuer_name": r.issuer_name, "title": r.title, "max_holders": int(r.max_holders),
                     "ticker": best[0], "yahoo_symbol": sec_reference.yahoo_symbol(best[0]) if best[0] else None,
                     "issuer_cik": tick2cik.get(best[0]) if best[0] else None, "n_checked": best[1],
                     "share_within_10pct": best[2], "method": best[3] if best[0] else best[3]})
    m = pd.DataFrame(rows)
    m.to_parquet(MAP_PATH, index=False)
    log.info("cusip map: %s", m.method.value_counts().to_dict())
    return m


# ------------------------------------------------------------------ split adjustment
def split_factors(m: pd.DataFrame, periods: list[pd.Timestamp]) -> pd.DataFrame:
    """Shares multiplier from period P-1 to P (e.g. 4.0 across a 4:1 split)."""
    rows = []
    pe = np.array(periods, dtype="datetime64[D]")
    for c, sym in m.dropna(subset=["yahoo_symbol"])[["cusip", "yahoo_symbol"]].itertuples(index=False):
        df = prices.get_prices(sym)
        if df is None:
            continue
        d = df["date"].values.astype("datetime64[D]")
        cum = (df["raw_close"] / df["close"]).values  # cumulative future split factor
        i = np.clip(np.searchsorted(d, pe, side="right") - 1, 0, len(d) - 1)
        f = cum[i]
        for k in range(1, len(pe)):
            ratio = f[k - 1] / f[k] if f[k] > 0 else 1.0
            if abs(ratio - 1) > 1e-6:
                rows.append((c, pd.Timestamp(pe[k]), ratio))
    return pd.DataFrame(rows, columns=["cusip", "period", "split"])


# ------------------------------------------------------------------ main build
def build(reuse_map: bool = False) -> None:
    con = duckdb.connect()
    con.execute("SET memory_limit='6GB'; SET threads=4")
    f = build_filings(con)
    m = pd.read_parquet(MAP_PATH) if (reuse_map and MAP_PATH.exists()) else build_cusip_map(con)
    mapped = m.dropna(subset=["yahoo_symbol"])[["cusip", "yahoo_symbol", "issuer_cik"]]
    fk = f[~f.dup][["accession", "filer_cik", "manager", "filing_date", "period", "deadline", "on_time", "aum", "n_positions"]]
    con.register("fk", fk)
    con.register("mapped", mapped)
    # Manager-period stats on the FULL report (all holdings), point-in-time.
    mg = con.execute(f"""
        WITH h AS (SELECT h.accession, h.value_usd,
                          row_number() OVER (PARTITION BY h.accession ORDER BY h.value_usd DESC) rk
                   FROM read_parquet('{sec_13f.glob()}') h JOIN fk USING (accession)),
             t AS (SELECT accession, sum(value_usd) top10_value FROM h WHERE rk <= 10 GROUP BY accession),
             e AS (SELECT accession, sum(value_usd) FILTER (WHERE regexp_matches(upper(issuer_name || ' ' || coalesce(title, '')),
                          '{ETF_PATTERN}')) etf_value
                   FROM read_parquet('{sec_13f.glob()}') JOIN fk USING (accession) GROUP BY accession)
        SELECT fk.*, t.top10_value / nullif(fk.aum, 0) top10, coalesce(e.etf_value, 0) / nullif(fk.aum, 0) etf_share
        FROM fk JOIN t USING (accession) LEFT JOIN e USING (accession)""").df()
    # 'Hedge-fund-like' = concentrated, active stock picker. ETF-heavy advisers (whose top-10 are index funds)
    # and strategic single-stock holders (foundations, corporate stakes) are excluded.
    mg["concentrated"] = ((mg.aum >= CONC_MIN_AUM) & mg.n_positions.between(CONC_MIN_POSITIONS, CONC_MAX_POSITIONS)
                          & (mg.top10 >= CONC_MIN_TOP10) & (mg.etf_share < CONC_MAX_ETF_SHARE))
    mg["notable"] = mg.filer_cik.isin(list(NOTABLE))
    mg.to_parquet(MGR_PATH, index=False)
    log.info("managers: %d manager-periods, %.1f%% concentrated", len(mg), 100 * mg.concentrated.mean())

    periods = sorted(mg.period.unique())
    sf = split_factors(m, periods)
    con.register("sf", sf)
    con.register("mg", mg[["accession", "filer_cik", "period", "filing_date", "deadline", "on_time", "aum",
                           "concentrated", "notable"]])
    # Mapped holdings per manager-period, with the previous quarter's (split-adjusted) position.
    con.execute(f"""
        CREATE TABLE h AS
        SELECT mg.filer_cik, mg.period, mg.filing_date, mg.deadline, mg.on_time, mg.concentrated, mg.notable, mg.aum,
               x.cusip, sum(x.shares) shares, sum(x.value_usd) value_usd
        FROM read_parquet('{sec_13f.glob()}') x JOIN mg USING (accession) JOIN mapped USING (cusip)
        GROUP BY ALL""")
    con.execute("""
        CREATE TABLE pairs AS
        WITH cur AS (SELECT * FROM h), prv AS (SELECT * FROM h)
        SELECT coalesce(c.filer_cik, p.filer_cik) filer_cik,
               coalesce(c.period, p.period + INTERVAL 3 MONTH) period_raw, coalesce(c.cusip, p.cusip) cusip,
               c.shares shares, p.shares * coalesce(sf.split, 1.0) prev_shares, c.value_usd, c.aum,
               c.filing_date, c.deadline, c.on_time, coalesce(c.concentrated, p.concentrated) concentrated,
               coalesce(c.notable, p.notable) notable
        FROM cur c FULL OUTER JOIN prv p
          ON c.filer_cik = p.filer_cik AND c.cusip = p.cusip AND c.period = last_day(p.period + INTERVAL 3 MONTH)
        LEFT JOIN sf ON sf.cusip = coalesce(c.cusip, p.cusip) AND sf.period = coalesce(c.period, last_day(p.period + INTERVAL 3 MONTH))""")
    # Restrict change metrics to managers that filed (on time) in both quarters.
    mgp = mg[mg.on_time][["filer_cik", "period"]].copy()
    both = mgp.merge(mgp.assign(period=mgp.period + pd.offsets.QuarterEnd(1)), on=["filer_cik", "period"])
    con.register("filed_both", both)
    stock = con.execute("""
        WITH p AS (SELECT pairs.*, last_day(period_raw) period FROM pairs),
        b AS (SELECT p.* FROM p JOIN filed_both USING (filer_cik, period)),
        cls AS (SELECT *, CASE WHEN prev_shares IS NULL OR prev_shares = 0 THEN 'new'
                               WHEN shares IS NULL THEN 'exit'
                               WHEN shares / prev_shares - 1 >= 0.25 THEN 'incr'
                               WHEN shares / prev_shares - 1 <= -0.25 THEN 'decr' ELSE 'hold' END st FROM b)
        SELECT cusip, period,
               count(*) FILTER (WHERE shares > 0) n_holders_both,
               count(*) FILTER (WHERE prev_shares > 0) n_prev_both,
               count(*) FILTER (WHERE st = 'new') n_new, count(*) FILTER (WHERE st = 'exit') n_exit,
               count(*) FILTER (WHERE concentrated AND st = 'new') c_new,
               count(*) FILTER (WHERE concentrated AND st = 'incr') c_incr,
               count(*) FILTER (WHERE concentrated AND st = 'decr') c_decr,
               count(*) FILTER (WHERE concentrated AND st = 'exit') c_exit,
               count(*) FILTER (WHERE concentrated AND shares > 0) c_holders,
               sum(shares) FILTER (WHERE concentrated) c_shares, sum(prev_shares) FILTER (WHERE concentrated) c_prev_shares,
               sum(shares) inst_shares, sum(prev_shares) inst_prev_shares
        FROM cls GROUP BY cusip, period""").df()
    allh = con.execute("""SELECT cusip, period, count(*) n_holders, sum(value_usd) inst_value, sum(shares) shares_all
                          FROM h WHERE on_time GROUP BY cusip, period""").df()
    nboth = both.groupby("period").size().rename("n_filers_both").reset_index()
    stock = stock.merge(allh, on=["cusip", "period"], how="left").merge(nboth, on="period", how="left")
    stock = stock.merge(mapped, on="cusip", how="inner")
    stock["breadth_change"] = (stock.n_holders_both - stock.n_prev_both) / stock.n_filers_both
    stock["signal_date"] = deadline(stock["period"])
    stock = stock[stock.period >= pd.Timestamp("2013-09-30")]  # first quarter with a prior quarter in the data
    stock.to_parquet(STOCK_PATH, index=False)
    log.info("stock-periods: %d", len(stock))

    # Manager-level new positions, timed at that manager's own filing date.
    ev = con.execute("""
        SELECT p.filer_cik, last_day(p.period_raw) period, p.cusip, p.filing_date, p.value_usd / nullif(p.aum, 0) weight,
               p.concentrated, p.notable
        FROM pairs p JOIN filed_both b ON b.filer_cik = p.filer_cik AND b.period = last_day(p.period_raw)
        WHERE (p.prev_shares IS NULL OR p.prev_shares = 0) AND p.shares > 0 AND (p.concentrated OR p.notable)""").df()
    ev = ev.merge(mapped, on="cusip").merge(mg[["filer_cik", "period", "manager"]], on=["filer_cik", "period"], how="left")
    ev.to_parquet(MGR_EVENTS_PATH, index=False)
    # Recent manager-level holdings for the Radar (last 8 quarters).
    recent = con.execute(f"""
        SELECT h.filer_cik, h.period, h.filing_date, h.cusip, h.shares, h.value_usd, h.aum, h.concentrated, h.notable
        FROM h WHERE h.period >= (SELECT max(period) FROM h) - INTERVAL 24 MONTH""").df()
    recent = recent.merge(mg[["filer_cik", "period", "manager"]], on=["filer_cik", "period"], how="left")
    recent.sort_values(["cusip", "period"]).to_parquet(RECENT_PATH, index=False)
    con.close()
