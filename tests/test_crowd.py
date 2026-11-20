"""Crowd-attention tests: the GameStop known answer and timing."""
import pandas as pd
import pytest

from smartmoney import config

HAS_WIKI = (config.RAW / "wiki" / "GME.parquet").exists()


@pytest.mark.skipif(not HAS_WIKI, reason="run `python -m smartmoney.pipeline wiki` first")
def test_gamestop_january_2021_attention_spike():
    from smartmoney.research.crowd_study import known_answers
    k = known_answers()["gme_2021"]
    assert "2021-01-25" <= k["peak_day"] <= "2021-02-02"
    assert k["ratio"] > 20 and k["peak_views"] > 100_000


def test_attention_is_public_the_next_day():
    from smartmoney.research.crowd_study import SIGNAL_EVENTS_PATH
    if not SIGNAL_EVENTS_PATH.exists():
        pytest.skip("crowd study not run")
    se = pd.read_parquet(SIGNAL_EVENTS_PATH)
    assert (se.filing_date == se.day + pd.Timedelta(days=1)).all()
    assert (se.entry_date > se.filing_date).all()
    assert (se.sig_date <= se.filing_date).all()
