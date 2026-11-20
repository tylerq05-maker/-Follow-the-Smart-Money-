"""Known-answer tests against well-documented historical filings."""
import pandas as pd
import pytest

from tests.conftest import needs_data


@needs_data
def test_dimon_jpm_purchase_feb_2016():
    """Jamie Dimon bought 500,000 JPM shares (~$26.6M) on 11 Feb 2016, filed the same day."""
    from smartmoney.insiders.signals import load_classified
    t = load_classified(filters=[("issuer_cik", "==", 19617), ("trans_code", "==", "P")])
    d = t[(t.owner_name == "DIMON JAMES") & (t.trans_date == "2016-02-11")]
    assert d.shares.sum() == 500_000
    assert d.value.sum() == pytest.approx(26.59e6, rel=0.005)
    assert (d.filing_date == "2016-02-11").all() and (d.quality == "discretionary_buy").all()
    assert (d.role == "CEO").all()


@needs_data
def test_berkshire_oxy_accumulation_2022():
    """Berkshire Hathaway's 2022 Occidental purchases are open-market buys by a 10% owner."""
    from smartmoney.insiders.signals import load_classified
    t = load_classified(filters=[("issuer_cik", "==", 797468), ("owner_cik", "==", 1067983)])
    b = t[(t.trans_code == "P") & (t.filing_date.dt.year == 2022)]
    assert b.value.sum() > 5e9
    assert set(b.role) == {"10% owner"} and not b.is_insider.any()


@needs_data
def test_10b5_1_sales_are_flagged_after_2023_rule():
    """After the 2023 amendments most large-cap executive sales carry the 10b5-1 checkbox."""
    from smartmoney.insiders.signals import load_classified
    t = load_classified(columns=["trans_code", "filing_date", "quality", "aff10b5one"],
                        filters=[("trans_code", "==", "S")])
    recent = t[t.filing_date >= "2024-01-01"]
    assert recent.aff10b5one.mean() > 0.3
    assert (recent.quality == "planned_sell").mean() > 0.4
