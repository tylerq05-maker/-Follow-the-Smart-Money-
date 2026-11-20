import numpy as np
import statsmodels.api as sm

from smartmoney.research.stats import clustered_mean, cluster_bootstrap_ci, holm, newey_west_ols


def test_clustered_mean_matches_statsmodels():
    rng = np.random.default_rng(0)
    g = np.repeat(np.arange(40), 25)
    x = rng.normal(0.01, 0.05, len(g)) + rng.normal(0, 0.03, 40)[g]  # strong within-cluster correlation
    ours = clustered_mean(x, g)
    ref = sm.OLS(x, np.ones(len(x))).fit(cov_type="cluster", cov_kwds={"groups": g})
    assert np.isclose(ours["se"], ref.bse[0], rtol=1e-6)
    iid_se = x.std(ddof=1) / np.sqrt(len(x))
    assert ours["se"] > iid_se * 2  # clustering matters for correlated events


def test_bootstrap_ci_brackets_mean():
    rng = np.random.default_rng(1)
    x = rng.normal(0.02, 0.1, 2000)
    lo, hi = cluster_bootstrap_ci(x, np.arange(2000) // 20)
    assert lo < x.mean() < hi


def test_holm():
    adj = holm({"a": 0.01, "b": 0.04, "c": 0.03, "d": None})
    assert np.isclose(adj["a"], 0.03) and np.isclose(adj["c"], 0.06) and np.isclose(adj["b"], 0.06)
    assert adj["d"] is None


def test_newey_west_recovers_coefficients():
    rng = np.random.default_rng(2)
    x = rng.normal(size=3000)
    y = 0.001 + 1.5 * x + rng.normal(scale=0.5, size=3000)
    r = newey_west_ols(y, np.column_stack([np.ones(3000), x]), lags=5)
    assert abs(r["coef"][1] - 1.5) < 0.05 and r["t"][1] > 50
