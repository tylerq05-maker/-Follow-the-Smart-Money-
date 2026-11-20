"""Look-ahead bias tests: every input to a signal must be public by its filing date."""
import numpy as np
import pandas as pd

from smartmoney.insiders.signals import _rolling_distinct, apply_cooldown, classify_traders
from tests.conftest import needs_results


def test_rolling_window_excludes_future_filings():
    d = np.array(["2024-01-01", "2024-01-10", "2024-01-20", "2024-02-25"], dtype="datetime64[D]")
    o = np.array([1, 2, 3, 4])
    at = np.array(["2024-01-10", "2024-01-20", "2024-02-25"], dtype="datetime64[D]")
    assert list(_rolling_distinct(d, o, at, 30)) == [2, 3, 1]


def test_trader_type_uses_prior_years_only():
    bits = pd.Series({(1, 9, 2021): 1, (1, 9, 2022): 1, (1, 9, 2023): 1, (1, 9, 2024): 2,
                      (2, 9, 2021): 1, (2, 9, 2022): 2, (2, 9, 2023): 4})
    keys = pd.DataFrame({"owner_cik": [1, 2, 1, 3], "issuer_cik": [9, 9, 9, 9], "year": [2024, 2024, 2023, 2024]})
    assert list(classify_traders(keys, bits)) == ["routine", "opportunistic", "unclassified", "unclassified"]


def test_cooldown():
    d = pd.Series(pd.to_datetime(["2024-01-01", "2024-01-15", "2024-02-05", "2024-01-03"]))
    i = pd.Series([1, 1, 1, 2])
    assert list(apply_cooldown(d, i, 30)) == [True, False, True, True]


@needs_results
def test_signal_events_respect_disclosure_timing():
    from smartmoney.research.insider_study import SIGNAL_EVENTS_PATH
    se = pd.read_parquet(SIGNAL_EVENTS_PATH)
    assert (se.entry_date > se.filing_date).all()
    assert (se.sig_date <= se.filing_date).all()


@needs_results
def test_cluster_counts_recomputed_from_raw_filings():
    """Recompute n_insiders_30d for sampled cluster events using only filings dated <= the event."""
    from smartmoney.insiders.signals import load_classified
    from smartmoney.research.insider_study import SIGNAL_EVENTS_PATH
    se = pd.read_parquet(SIGNAL_EVENTS_PATH)
    ev = se[se.signal == "BUY_CLUSTER"].sample(25, random_state=0)
    tx = load_classified(columns=["issuer_cik", "owner_cik", "filing_date", "quality", "is_insider"],
                         filters=[("issuer_cik", "in", ev.issuer_cik.unique().tolist())])
    tx = tx[(tx.quality == "discretionary_buy") & tx.is_insider]
    for r in ev.itertuples():
        w = tx[(tx.issuer_cik == r.issuer_cik) & (tx.filing_date <= r.filing_date)
               & (tx.filing_date > r.filing_date - pd.Timedelta(days=30))]
        assert w.owner_cik.nunique() == r.n_insiders_30d >= 3
