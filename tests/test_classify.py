import numpy as np
import pandas as pd
import pytest

from smartmoney.insiders.classify import classify, dedupe_joint_reports, parse_title


@pytest.mark.parametrize("title,ceo,cfo", [
    ("President and CEO", True, False), ("Chairman & CEO", True, False), ("CEO CIB", False, False),
    ("CEO, Consumer & Community Banking", False, False), ("EVP & CFO", False, True),
    ("SVP, CFO and Treasurer", False, True), ("Chief Accounting Officer", False, False),
    ("Co-CEO", True, False), ("CFO, Americas", False, False), ("Director", False, False),
])
def test_titles(title, ceo, cfo):
    r = parse_title(title)
    assert r["is_ceo"] == ceo and r["is_cfo"] == cfo


def _row(**kw):
    base = dict(accession="a1", trans_sk=1, filing_date=pd.Timestamp("2024-01-05"), doc_type="4", issuer_cik=1,
                issuer_name="X", issuer_symbol="X", owner_cik=10, owner_name="A", is_director=True, is_officer=False,
                is_ten_pct=False, is_other=False, officer_title=None, security_title="Common Stock",
                trans_date=pd.Timestamp("2024-01-03"), trans_code="P", equity_swap=False, timeliness="",
                shares=1000.0, price=10.0, acq_disp="A", shares_after=5000.0, direct=True, nature=None,
                aff10b5one=False, filing_plan_mention=False, fn_plan=False, fn_drip=False, fn_espp=False,
                fn_offering=False)
    base.update(kw)
    return base


@pytest.mark.parametrize("kw,reason", [
    ({}, ""), ({"trans_code": "A"}, "not_open_market_trade"), ({"trans_code": "F"}, "not_open_market_trade"),
    ({"trans_code": "G"}, "not_open_market_trade"), ({"doc_type": "4/A"}, "amendment_or_form5"),
    ({"security_title": "Series A Preferred Stock"}, "non_common_security"), ({"price": 0.0}, "missing_price_or_shares"),
    ({"fn_drip": True}, "drip_espp_plan"), ({"fn_offering": True}, "offering_private_placement"),
    ({"aff10b5one": True}, "rule_10b5_1_plan"), ({"trans_date": pd.Timestamp("2024-01-08")}, "invalid_dates"),
    ({"acq_disp": "D"}, "inconsistent_direction"),
])
def test_exclusion_reasons(kw, reason):
    c = classify(pd.DataFrame([_row(**kw)]))
    assert c.iloc[0].exclusion_reason == reason


def test_late_filing_and_value():
    c = classify(pd.DataFrame([_row(trans_date=pd.Timestamp("2023-12-20"))]))
    assert c.iloc[0].late_filing and c.iloc[0].value == 10_000
    assert c.iloc[0].holding_change == pytest.approx(1000 / 4000)


def test_dedupe_keeps_distinct_directors_removes_echoes():
    rows = [_row(accession="a1", owner_cik=10), _row(accession="a2", owner_cik=11),          # two directors
            _row(accession="a3", owner_cik=12, is_director=False, is_ten_pct=True),         # fund echo
            _row(accession="a4", owner_cik=10),                                              # same person refiled
            _row(accession="a1", trans_sk=2, owner_cik=10)]                                  # split lot, same filing
    c = classify(pd.DataFrame(rows))
    d, n = dedupe_joint_reports(c)
    assert n == 2
    assert sorted(d.accession) == ["a1", "a1", "a2"]
