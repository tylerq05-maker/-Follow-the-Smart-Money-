"""13F tests: timing, identity, units, split handling and known answers."""
import pandas as pd
import pytest

from smartmoney import config
from smartmoney.institutions.build import deadline, norm_name

HAS_13F = (config.PROCESSED / "f13_stock_period.parquet").exists()
needs_13f = pytest.mark.skipif(not HAS_13F, reason="run `python -m smartmoney.pipeline build13f` first")


def test_deadline_is_45_days_rolled_to_business_day():
    p = pd.Series(pd.to_datetime(["2024-03-31", "2022-03-31", "2021-12-31"]))
    assert list(deadline(p).dt.strftime("%Y-%m-%d")) == ["2024-05-15", "2022-05-16", "2022-02-14"]  # 2022-05-15 is a Sunday


def test_name_normalisation():
    assert norm_name("APPLE INC") == norm_name("Apple Inc.") == "APPLE"
    assert norm_name("JPMORGAN CHASE & CO") == norm_name("JPMorgan Chase & Co.")
    assert norm_name("BERKSHIRE HATHAWAY INC DEL") == norm_name("BERKSHIRE HATHAWAY INC")


@needs_13f
def test_berkshire_apple_known_answer():
    """Berkshire reported 887,135,554 AAPL shares for Q4 2021 (13F filed Feb 2022) — use that quarter, not 'today'."""
    import duckdb
    from smartmoney.sources import sec_13f
    r = duckdb.connect().execute(f"""SELECT sum(shares) s, min(filing_date) f FROM read_parquet('{sec_13f.glob()}')
        WHERE filer_cik = 1067983 AND cusip = '037833100' AND period = '2021-12-31'""").fetchone()
    assert r[0] == 887_135_554
    assert pd.Timestamp(r[1]) > pd.Timestamp("2021-12-31") + pd.Timedelta(days=30)


@needs_13f
def test_value_units_consistent_across_2023_switch():
    """Implied AAPL price = value/shares must equal the quarter-end close both before (thousands) and after (dollars)."""
    import duckdb
    from smartmoney.sources import sec_13f
    q = duckdb.connect().execute(f"""SELECT period, median(value_usd / shares) px FROM read_parquet('{sec_13f.glob()}')
        WHERE cusip = '037833100' AND period IN ('2021-12-31', '2023-06-30') GROUP BY period ORDER BY period""").fetchall()
    assert q[0][1] == pytest.approx(177.57, rel=0.01) and q[1][1] == pytest.approx(193.97, rel=0.01)


@needs_13f
def test_cusip_map_verified():
    from smartmoney.institutions.build import MAP_PATH
    m = pd.read_parquet(MAP_PATH).set_index("cusip")
    assert m.loc["037833100", "ticker"] == "AAPL" and m.loc["037833100", "share_within_10pct"] >= 0.9
    assert (m.dropna(subset=["ticker"]).share_within_10pct >= 0.7).all()


@needs_13f
def test_split_does_not_look_like_buying():
    """NVDA split 10:1 on 2024-06-10. Institutional share counts must be split-adjusted across Q1->Q2 2024."""
    from smartmoney.institutions.build import STOCK_PATH
    s = pd.read_parquet(STOCK_PATH)
    r = s[(s.yahoo_symbol == "NVDA") & (s.period == "2024-06-30")].iloc[0]
    assert 0.8 < r.inst_shares / r.inst_prev_shares < 1.25


@needs_13f
def test_signals_are_dated_after_disclosure():
    from smartmoney.research.institution_study import SIGNAL_EVENTS_PATH
    if not SIGNAL_EVENTS_PATH.exists():
        pytest.skip("institution study not run")
    se = pd.read_parquet(SIGNAL_EVENTS_PATH)
    assert (se.filing_date > se.period).all()
    stock = se[se.signal.str.startswith(("INST_", "HF_CROWD", "HF_ACCUM"))]
    assert (stock.filing_date >= stock.period + pd.Timedelta(days=45)).all()
    assert (se.entry_date > se.filing_date).all()
