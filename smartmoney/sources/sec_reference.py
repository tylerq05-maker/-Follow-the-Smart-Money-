"""Company reference data: CIK <-> ticker resolution with provenance.

The SEC is the authority on *who* a company is (CIK). Tickers are not stable
identifiers: they get renamed (FB -> META) and REUSED by unrelated companies
after delisting (e.g. Yahoo's 'FB' today is a different security). We therefore
key everything on CIK and record how each CIK's price symbol was chosen; the
price-reconciliation step later verifies the choice against the prices insiders
actually reported paying.
"""
from __future__ import annotations

import json
import logging

import pandas as pd

from .. import config, http

log = logging.getLogger(__name__)

TICKERS_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
REF_PATH = config.PROCESSED / "issuer_symbols.parquet"


def yahoo_symbol(sym: str) -> str:
    return sym.strip().upper().replace(".", "-").replace("/", "-")


def sec_ticker_table() -> pd.DataFrame:
    """Current SEC CIK/ticker/exchange table (one row per listed ticker)."""
    cache = config.RAW / "sec_company_tickers_exchange.json"
    if not cache.exists():
        cache.write_bytes(http.get(TICKERS_URL).content)
    raw = json.loads(cache.read_text())
    df = pd.DataFrame(raw["data"], columns=raw["fields"])
    df["cik"] = df["cik"].astype("int64")
    df["ticker"] = df["ticker"].str.upper()
    return df


def build_reference() -> pd.DataFrame:
    """Resolve one price symbol per issuer CIK seen in the insider data."""
    from . import sec_insider_bulk as bulk

    tx = bulk.load(columns=["issuer_cik", "issuer_name", "issuer_symbol", "filing_date"])
    tx = tx.dropna(subset=["issuer_cik"]).sort_values("filing_date")
    last = tx.groupby("issuer_cik").agg(
        issuer_name=("issuer_name", "last"), last_filing_symbol=("issuer_symbol", "last"),
        first_filing=("filing_date", "min"), last_filing=("filing_date", "max"),
        n_symbols=("issuer_symbol", "nunique"))
    sec = sec_ticker_table()
    cur = sec.groupby("cik").agg(sec_tickers=("ticker", list), exchange=("exchange", "first"),
                                 sec_name=("name", "first"))
    ref = last.join(cur, how="left")

    def pick(row):
        tickers = row["sec_tickers"] if isinstance(row["sec_tickers"], list) else []
        fs = (row["last_filing_symbol"] or "").upper()
        if tickers:
            if fs in tickers:
                return fs, "sec_current_match"
            return tickers[0], "sec_current"
        if fs and fs not in {"NONE", "N/A", "NA"}:
            return fs, "filing_symbol_unverified"
        return None, "unresolved"

    picks = ref.apply(pick, axis=1, result_type="expand")
    ref["symbol"], ref["symbol_method"] = picks[0], picks[1]
    ref["yahoo_symbol"] = ref["symbol"].map(lambda s: yahoo_symbol(s) if isinstance(s, str) else None)
    ref["currently_listed"] = ref["sec_tickers"].map(lambda x: isinstance(x, list) and len(x) > 0)
    ref = ref.drop(columns=["sec_tickers"]).reset_index()
    ref.to_parquet(REF_PATH, index=False)
    log.info("reference: %d issuers; methods=%s", len(ref), ref.symbol_method.value_counts().to_dict())
    return ref


def load_reference() -> pd.DataFrame:
    return pd.read_parquet(REF_PATH)


# ------------------------------------------------------------- live company data
SIC_SECTORS = [  # (lo, hi, sector, SPDR sector ETF)
    (100, 999, "Materials", "XLB"), (1000, 1499, "Materials", "XLB"), (1300, 1399, "Energy", "XLE"),
    (1500, 1799, "Industrials", "XLI"), (2000, 2199, "Consumer Staples", "XLP"),
    (2200, 2799, "Consumer Discretionary", "XLY"), (2800, 2829, "Materials", "XLB"),
    (2830, 2836, "Health Care", "XLV"), (2840, 2899, "Materials", "XLB"), (2900, 2999, "Energy", "XLE"),
    (3000, 3569, "Industrials", "XLI"), (3570, 3579, "Technology", "XLK"), (3580, 3659, "Industrials", "XLI"),
    (3660, 3699, "Technology", "XLK"), (3700, 3799, "Consumer Discretionary", "XLY"),
    (3800, 3849, "Health Care", "XLV"), (3850, 3999, "Industrials", "XLI"),
    (4000, 4799, "Industrials", "XLI"), (4800, 4899, "Communication Services", "XLC"),
    (4900, 4999, "Utilities", "XLU"), (5000, 5199, "Industrials", "XLI"),
    (5200, 5999, "Consumer Discretionary", "XLY"), (6000, 6499, "Financials", "XLF"),
    (6500, 6799, "Real Estate", "XLRE"), (7000, 7369, "Consumer Discretionary", "XLY"),
    (7370, 7379, "Technology", "XLK"), (7380, 7999, "Industrials", "XLI"),
    (8000, 8099, "Health Care", "XLV"), (8100, 8999, "Industrials", "XLI"),
]


def sic_to_sector(sic: int | None) -> tuple[str | None, str | None]:
    if sic is None:
        return None, None
    hit = None
    for lo, hi, sector, etf in SIC_SECTORS:  # later (narrower) ranges override earlier ones
        if lo <= sic <= hi:
            hit = (sector, etf)
    return hit or (None, None)


def submissions(cik: int) -> dict:
    return http.get(SUBMISSIONS_URL.format(cik=cik)).json()


def shares_outstanding(cik: int) -> tuple[float | None, str | None]:
    """Latest dei:EntityCommonStockSharesOutstanding from XBRL company facts."""
    r = http.get(FACTS_URL.format(cik=cik), ok_404=True)
    if r is None:
        return None, None
    facts = r.json().get("facts", {})
    pts = facts.get("dei", {}).get("EntityCommonStockSharesOutstanding", {}).get("units", {}).get("shares", [])
    if not pts:
        return None, None
    # Sum share classes reported for the same, latest date (e.g. GOOGL/GOOG).
    latest = max(p["end"] for p in pts)
    vals = {p.get("frame") or p.get("accn"): p["val"] for p in pts if p["end"] == latest}
    return float(sum(vals.values())), latest
