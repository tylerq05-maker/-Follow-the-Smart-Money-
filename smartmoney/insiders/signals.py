"""Point-in-time insider events and signal definitions.

Event = (issuer, filing date, side, group): all qualifying trades by one group
(officers/directors, 10% blockholders, or planned sellers) disclosed on one day.
Every feature of an event is computable from filings with filing_date <= the
event's filing date — this is enforced by construction and verified in
tests/test_lookahead.py.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd

from .. import config
from ..sources import sec_insider_bulk as bulk
from .classify import classify, dedupe_joint_reports

log = logging.getLogger(__name__)
CLASSIFIED_PATH = config.PROCESSED / "insider_tx_classified.parquet"
EVENTS_PATH = config.PROCESSED / "insider_events.parquet"


# ---------------------------------------------------------------- transactions
def build_classified() -> tuple[pd.DataFrame, dict]:
    # Research store = open-market trades only (P/S); other codes are served per issuer on demand.
    tx = bulk.load(filters=[("trans_code", "in", ["P", "S"])])
    n_raw = len(tx)
    dup_rows = int(tx.duplicated(["accession", "trans_sk"]).sum())
    tx = tx.drop_duplicates(["accession", "trans_sk"])
    tx = classify(tx)
    trades = tx["quality"] != "excluded"
    deduped, n_joint = dedupe_joint_reports(tx[trades])
    tx = pd.concat([deduped, tx[~trades]]).sort_values(["filing_date", "accession", "trans_sk"])
    tx.to_parquet(CLASSIFIED_PATH, index=False)
    funnel = (tx[tx.trans_code.isin(["P", "S"])].groupby(["trans_code", "exclusion_reason"]).size()
              .rename("n").reset_index())
    dq = {"n_raw_transactions": n_raw, "duplicate_rows_removed": dup_rows, "joint_filer_duplicates_removed": n_joint,
          "n_transactions": len(tx), "code_counts": tx["trans_code"].value_counts().to_dict(),
          "late_filing_share_open_market": float(tx.loc[trades & ~tx.invalid_date, "late_filing"].mean()),
          "invalid_date_rows": int(tx["invalid_date"].sum()),
          "funnel": funnel.to_dict("records"),
          "first_filing": str(tx.filing_date.min().date()), "last_filing": str(tx.filing_date.max().date())}
    return tx, dq


def load_classified(columns=None, filters=None) -> pd.DataFrame:
    return pd.read_parquet(CLASSIFIED_PATH, columns=columns, filters=filters)


# ------------------------------------------------------- routine vs opportunistic
def trader_history_bits(tx: pd.DataFrame) -> pd.DataFrame:
    """12-bit month masks of open-market trading per (owner, issuer, year).

    Uses FILING month, so the classification for year Y only depends on filings
    made before Y (strictly point-in-time). Cohen, Malloy & Pomorski (2012):
    an insider is ROUTINE if they traded in the same calendar month in each of
    the three prior years; OPPORTUNISTIC if they traded in each of the three
    prior years but not routinely; otherwise UNCLASSIFIED.
    """
    t = tx[tx["trans_code"].isin(["P", "S"]) & tx["is_insider"] & tx["owner_cik"].notna()]
    t = t[["owner_cik", "issuer_cik", "filing_date"]].dropna().drop_duplicates()
    t = t.assign(year=t.filing_date.dt.year, month=t.filing_date.dt.month)
    t = t.drop_duplicates(["owner_cik", "issuer_cik", "year", "month"])
    t["bit"] = np.left_shift(1, (t["month"] - 1).astype("int64").values)
    return t.groupby(["owner_cik", "issuer_cik", "year"])["bit"].sum()  # unique months: sum == bitwise OR


def classify_traders(keys: pd.DataFrame, bits: pd.Series) -> pd.Series:
    """keys: DataFrame[owner_cik, issuer_cik, year] -> 'routine'|'opportunistic'|'unclassified'."""
    b = bits.to_dict()
    out = []
    for o, i, y in keys[["owner_cik", "issuer_cik", "year"]].itertuples(index=False):
        m = [b.get((o, i, y - k), 0) for k in (1, 2, 3)]
        if not all(m):
            out.append("unclassified")
        elif m[0] & m[1] & m[2]:
            out.append("routine")
        else:
            out.append("opportunistic")
    return pd.Series(out, index=keys.index)


# ---------------------------------------------------------------------- events
def _rolling_distinct(dates: np.ndarray, owners: np.ndarray, at: np.ndarray, window_days: int) -> np.ndarray:
    """Distinct owners with filing date in (d - window, d] for each d in `at`."""
    order = np.argsort(dates, kind="stable")
    dates, owners = dates[order], owners[order]
    lo = np.searchsorted(dates, at - np.timedelta64(window_days, "D"), side="right")
    hi = np.searchsorted(dates, at, side="right")
    return np.array([len(np.unique(owners[a:b])) for a, b in zip(lo, hi)], dtype=int)


def _rolling_sum(dates: np.ndarray, vals: np.ndarray, at: np.ndarray, window_days: int) -> np.ndarray:
    order = np.argsort(dates, kind="stable")
    c = np.concatenate([[0.0], np.cumsum(vals[order])])
    d = dates[order]
    lo = np.searchsorted(d, at - np.timedelta64(window_days, "D"), side="right")
    hi = np.searchsorted(d, at, side="right")
    return c[hi] - c[lo]


def _evidence(g: pd.DataFrame) -> str:
    cols = ["owner_name", "role", "officer_title", "trans_date", "shares", "price", "value", "holding_change",
            "accession", "trader_type"]
    rows = g[cols].copy()
    rows["trans_date"] = rows["trans_date"].dt.strftime("%Y-%m-%d")
    rows["holding_change"] = rows["holding_change"].replace([np.inf], 9.99)
    return json.dumps(rows.round(4).where(rows.notna(), None).to_dict("records"))


def build_events(tx: pd.DataFrame) -> pd.DataFrame:
    tx = tx[tx["issuer_cik"].notna() & tx["filing_date"].notna()].copy()
    bits = trader_history_bits(tx)
    groups = {
        ("buy", "insider"): tx[(tx.quality == "discretionary_buy") & tx.is_insider],
        ("buy", "blockholder"): tx[(tx.quality == "discretionary_buy") & ~tx.is_insider & tx.is_ten_pct],
        ("sell", "insider"): tx[(tx.quality == "discretionary_sell") & tx.is_insider],
        ("sell", "planned"): tx[(tx.quality == "planned_sell") & tx.is_insider],
    }
    frames = []
    for (side, grp), t in groups.items():
        if t.empty:
            continue
        t = t.copy()
        keys = pd.DataFrame({"owner_cik": t.owner_cik, "issuer_cik": t.issuer_cik, "year": t.filing_date.dt.year})
        t["trader_type"] = classify_traders(keys, bits).values
        t = t.sort_values(["issuer_cik", "filing_date", "owner_cik", "trans_date"])
        # Previous same-side filing by the same insider at the same issuer (novelty).
        od = t.drop_duplicates(["issuer_cik", "owner_cik", "filing_date"])[["issuer_cik", "owner_cik", "filing_date"]]
        od["prev_filing"] = od.groupby(["issuer_cik", "owner_cik"])["filing_date"].shift()
        t = t.merge(od, on=["issuer_cik", "owner_cik", "filing_date"], how="left")
        t["first_in_2y"] = t["prev_filing"].isna() | ((t["filing_date"] - t["prev_filing"]).dt.days > 730)

        g = t.groupby(["issuer_cik", "filing_date"], sort=False)
        ev = g.agg(
            issuer_name=("issuer_name", "last"), issuer_symbol=("issuer_symbol", "last"),
            n_insiders=("owner_cik", "nunique"), n_tx=("trans_sk", "size"), value=("value", "sum"),
            shares=("shares", "sum"), has_ceo=("is_ceo", "any"), has_cfo=("is_cfo", "any"),
            has_chair=("is_chair", "any"), any_officer=("is_officer", "any"),
            max_holding_change=("holding_change", "max"), min_holding_change=("holding_change", "min"),
            first_trans_date=("trans_date", "min"), last_trans_date=("trans_date", "max"),
            max_lag_bdays=("filing_lag_bdays", "max"), any_late=("late_filing", "any"),
            any_first_in_2y=("first_in_2y", "any"),
        ).reset_index()
        ceo_cfo_val = t[t.is_ceo | t.is_cfo].groupby(["issuer_cik", "filing_date"])["value"].sum()
        ev = ev.merge(ceo_cfo_val.rename("ceo_cfo_value"), on=["issuer_cik", "filing_date"], how="left")
        ev["ceo_cfo_value"] = ev["ceo_cfo_value"].fillna(0.0)
        # Trader-type counts are per insider, not per transaction.
        tt = t.drop_duplicates(["issuer_cik", "filing_date", "owner_cik"]).groupby(
            ["issuer_cik", "filing_date"])["trader_type"].value_counts().unstack(fill_value=0)
        for c in ("opportunistic", "routine", "unclassified"):
            ev = ev.merge((tt[c] if c in tt else pd.Series(0, index=tt.index)).rename(f"n_{c}"),
                          on=["issuer_cik", "filing_date"], how="left", suffixes=("_drop", ""))
        ev = ev.drop(columns=[c for c in ev.columns if c.endswith("_drop")])
        ev["vwap"] = ev["value"] / ev["shares"]

        # Rolling 30-day (by filing date) aggregates within the same issuer/side/group.
        n30, v30 = np.zeros(len(ev), int), np.zeros(len(ev))
        tix = t.groupby("issuer_cik").indices
        for cik, idx in ev.groupby("issuer_cik").indices.items():
            tt_ = t.iloc[tix[cik]]
            at = ev["filing_date"].values[idx].astype("datetime64[D]")
            n30[idx] = _rolling_distinct(tt_["filing_date"].values.astype("datetime64[D]"),
                                         tt_["owner_cik"].values.astype("int64"), at, config.CLUSTER_WINDOW_DAYS)
            v30[idx] = _rolling_sum(tt_["filing_date"].values.astype("datetime64[D]"),
                                    tt_["value"].fillna(0).values, at, config.CLUSTER_WINDOW_DAYS)
        ev["n_insiders_30d"], ev["value_30d"] = n30, v30
        ev["side"], ev["group"] = side, grp
        frames.append(ev)
        log.info("events %s/%s: %d", side, grp, len(ev))
    events = pd.concat(frames, ignore_index=True)
    events["event_id"] = (events["side"].str[0] + events["group"].str[:2] + "-" + events["issuer_cik"].astype(str)
                          + "-" + events["filing_date"].dt.strftime("%Y%m%d"))
    return events


# --------------------------------------------------------------------- signals
@dataclass(frozen=True)
class Signal:
    key: str
    name: str
    direction: int  # +1 bullish hypothesis, -1 bearish, 0 control (no expected effect)
    side: str
    group: str
    definition: str
    rationale: str
    rule: Callable[[pd.DataFrame], pd.Series] = field(repr=False)


def _v(e):
    return e["value"] >= config.MIN_PURCHASE_VALUE


SIGNALS: list[Signal] = [
    Signal("BUY_ANY", "Any insider purchase", 1, "buy", "insider",
           "Discretionary open-market purchase (code P, non-plan, common stock) by an officer or director, "
           f">= ${config.MIN_PURCHASE_VALUE:,} disclosed on one day.",
           "Baseline 'follow every insider purchase' strategy (Lakonishok & Lee 2001; Seyhun 1986).", _v),
    Signal("BUY_CLUSTER", "Cluster buying", 1, "buy", "insider",
           f">= {config.CLUSTER_MIN_INSIDERS} distinct officers/directors with discretionary purchases filed within "
           f"{config.CLUSTER_WINDOW_DAYS} calendar days (triggers on the filing that completes the cluster).",
           "Independent agreement among insiders is harder to explain by liquidity or diversification needs.",
           lambda e: _v(e) & (e["n_insiders_30d"] >= config.CLUSTER_MIN_INSIDERS)),
    Signal("BUY_CEO_CFO", "CEO / CFO purchase", 1, "buy", "insider",
           "Discretionary purchase by the company CEO or CFO (divisional titles excluded).",
           "Top executives have the broadest information set (Wang, Shin & Francis 2012 on CFOs).",
           lambda e: e["ceo_cfo_value"] >= config.MIN_PURCHASE_VALUE),
    Signal("BUY_LARGE", "Large purchase (>= $1M)", 1, "buy", "insider",
           "Officer/director purchases totalling >= $1,000,000 disclosed on one day.",
           "Large personal capital commitment is a costly, credible signal.",
           lambda e: e["value"] >= 1_000_000),
    Signal("BUY_CONVICTION", "Holdings increase >= 20%", 1, "buy", "insider",
           "An insider increases directly-held shares by >= 20% in one purchase (relative to pre-trade holdings).",
           "Purchase size relative to the insider's own wealth in the stock, not absolute dollars, measures conviction.",
           lambda e: _v(e) & (e["max_holding_change"] >= 0.20)),
    Signal("BUY_DIP", "Buying into weakness", 1, "buy", "insider",
           "Officer/director purchase after the stock fell >= 20% over the prior 60 trading days.",
           "Contrarian insider buying: insiders may judge the decline as overdone (Jenter 2005).",
           lambda e: _v(e) & (e["prior_ret_60d"] <= -0.20)),
    Signal("BUY_OPPORTUNISTIC", "Opportunistic insider", 1, "buy", "insider",
           "Purchase by an insider who traded in each of the 3 prior years but NOT in the same calendar month "
           "(Cohen, Malloy & Pomorski 2012).",
           "Routine traders follow calendars; deviations from routine carry the information.",
           lambda e: _v(e) & (e["n_opportunistic"] > 0)),
    Signal("BUY_FIRST_2Y", "First purchase in 2+ years", 1, "buy", "insider",
           "Insider's first discretionary purchase in this stock in more than 730 days (or ever, in the data).",
           "Unusual activity relative to the insider's own history.",
           lambda e: _v(e) & e["any_first_in_2y"]),
    Signal("BUY_BLOCKHOLDER", "10% blockholder purchase", 1, "buy", "blockholder",
           "Discretionary purchase >= $100,000 by a 10% owner who is not an officer or director.",
           "Activist / concentrated holders adding exposure (a bridge to the institutional analysis).",
           lambda e: e["value"] >= 100_000),
    Signal("SELL_CLUSTER", "Cluster selling (discretionary)", -1, "sell", "insider",
           f">= {config.CLUSTER_MIN_INSIDERS} distinct officers/directors with discretionary (non-10b5-1) sales "
           f"filed within {config.CLUSTER_WINDOW_DAYS} days.",
           "Sales are noisier (diversification, liquidity, taxes) — tested, not assumed, to be bearish.",
           lambda e: e["n_insiders_30d"] >= config.CLUSTER_MIN_INSIDERS),
    Signal("SELL_CEO_CFO", "CEO / CFO discretionary sale >= $1M", -1, "sell", "insider",
           "Discretionary (non-plan) sale of >= $1,000,000 by the CEO or CFO.",
           "Largest discretionary disposals by the best-informed executives.",
           lambda e: e["ceo_cfo_value"] >= 1_000_000),
    Signal("SELL_PLANNED", "Rule 10b5-1 planned sales (control)", 0, "sell", "planned",
           "Officer/director sales flagged as made under a Rule 10b5-1 plan (checkbox or footnote).",
           "Pre-scheduled trades should carry little information: a built-in control group.",
           lambda e: e["value"] >= config.MIN_PURCHASE_VALUE),
]
SIGNAL_BY_KEY = {s.key: s for s in SIGNALS}


def apply_cooldown(dates: pd.Series, issuers: pd.Series, days: int = config.SIGNAL_COOLDOWN_DAYS) -> np.ndarray:
    """Keep the first trigger per issuer, then suppress triggers within `days` of the last kept one."""
    keep = np.zeros(len(dates), bool)
    order = np.lexsort((dates.values, issuers.values))
    last_cik, last_d = None, None
    dv, iv = dates.values, issuers.values
    for k in order:
        if iv[k] != last_cik or (dv[k] - last_d) > np.timedelta64(days, "D"):
            keep[k] = True
            last_cik, last_d = iv[k], dv[k]
    return keep


def signal_events(events: pd.DataFrame) -> pd.DataFrame:
    """Long table: one row per (signal, event) after cooldown."""
    out = []
    for s in SIGNALS:
        e = events[(events.side == s.side) & (events.group == s.group)]
        e = e[s.rule(e).fillna(False).astype(bool)]
        if e.empty:
            continue
        e = e[apply_cooldown(e["filing_date"], e["issuer_cik"])]
        out.append(e.assign(signal=s.key, direction=s.direction))
    return pd.concat(out, ignore_index=True)
