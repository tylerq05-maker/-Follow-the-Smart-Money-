from pathlib import Path

import pandas as pd
import pytest

from smartmoney.insiders.classify import classify
from smartmoney.sources.sec_form4_xml import parse_form4

FIX = Path(__file__).parent / "fixtures" / "form4_sample.xml"


def test_parse_fields():
    df = parse_form4(FIX.read_bytes(), "0000000000-24-000001", "2024-03-04")
    assert len(df) == 2
    buy, sell = df.iloc[0], df.iloc[1]
    assert buy.trans_code == "P" and buy.shares == 10_000 and buy.price == 25.5 and buy.acq_disp == "A"
    assert buy.issuer_symbol == "EXMP" and buy.owner_cik == 999001 and buy.is_officer and buy.is_director
    assert buy.shares_after == 50_000 and buy.direct
    assert not buy.fn_plan            # weighted-average footnote is not a plan
    assert sell.fn_plan                # plan footnote is attached to the sale only
    assert buy.filing_plan_mention     # ...but the filing as a whole mentions a plan


def test_classification_of_parsed_filing():
    df = parse_form4(FIX.read_bytes(), "0000000000-24-000001", "2024-03-04")
    df["doc_type"] = "4"
    c = classify(df)
    # Conservative rule: any 10b5-1 language in the filing marks its trades as planned.
    assert set(c.quality) == {"planned_buy", "planned_sell"}
    assert c.iloc[0].role == "CEO"
    assert c.iloc[0].holding_change == pytest.approx(10_000 / 40_000)
    assert c.iloc[0].filing_lag_bdays == 1 and not c.iloc[0].late_filing
