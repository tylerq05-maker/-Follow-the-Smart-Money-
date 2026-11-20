import numpy as np
import pandas as pd

from smartmoney.research import returns as R
from smartmoney.sources.prices import remove_bad_ticks, split_factor_after


def _series(prices, start="2024-01-01"):
    d = pd.bdate_range(start, periods=len(prices)).values.astype("datetime64[D]")
    p = np.asarray(prices, float)
    return R.Series(d, p, p, np.full(len(p), 1e7))


def test_entry_is_first_trading_day_after_filing():
    s = _series(np.arange(100, 400))
    spy = _series(np.full(300, 100.0))
    # 2024-01-05 is a Friday; filing that day -> entry Monday 2024-01-08 (index 5).
    o = R.event_returns(s, np.array(["2024-01-05", "2024-01-06"], dtype="datetime64[D]"), spy, spy,
                        s.dates[-1], horizons=[1, 5])
    assert str(o["entry_date"][0]) == "2024-01-08" and str(o["sig_date"][0]) == "2024-01-05"
    assert str(o["entry_date"][1]) == "2024-01-08"            # Saturday filing -> Monday too
    assert np.isclose(o["ret_1"][0], 106 / 105 - 1)          # close(e+1)/close(e)
    assert np.isclose(o["spy_1"][0], 0.0)
    assert (o["entry_date"] > np.array(["2024-01-05", "2024-01-06"], dtype="datetime64[D]")).all()


def test_not_matured_vs_truncated():
    s = _series(np.linspace(10, 20, 30))
    spy = _series(np.full(300, 100.0))
    late = np.array([s.dates[-3]])
    live = R.event_returns(s, late, spy, spy, data_end=s.dates[-1], horizons=[20])
    assert np.isnan(live["ret_20"][0]) and not live["trunc_20"][0]          # horizon not elapsed: missing
    dead = R.event_returns(s, late, spy, spy, data_end=s.dates[-1] + np.timedelta64(400, "D"), horizons=[20])
    assert dead["trunc_20"][0] and np.isclose(dead["ret_20"][0], s.adj[-1] / s.adj[-2] - 1)  # delisted: last price


def test_prior_return_uses_only_signal_day_and_before():
    p = np.r_[np.full(70, 100.0), 50.0, np.full(40, 50.0)]
    s = _series(p)
    spy = _series(np.full(300, 100.0))
    fd = np.array([s.dates[69]])  # filing the day BEFORE the crash
    o = R.event_returns(s, fd, spy, spy, s.dates[-1], horizons=[1])
    assert np.isclose(o["prior_ret_60d"][0], 0.0)  # the crash on the entry day is not 'known'


def test_split_factor_and_bad_ticks():
    dates = pd.Series(pd.to_datetime(["2024-06-07", "2024-06-10", "2024-06-11"]))
    ts = int(pd.Timestamp("2024-06-10").timestamp())
    f = split_factor_after(dates, {"x": {"date": ts, "numerator": 10, "denominator": 1}})
    assert list(f) == [10, 1, 1]
    df = pd.DataFrame({"date": pd.bdate_range("2024-01-01", periods=5), "adjclose": [10, 10, 100, 10, 10.0]})
    clean, n = remove_bad_ticks(df)
    assert n == 1 and len(clean) == 4
