import numpy as np
from scipy import stats as sps
from sklearn.metrics import roc_auc_score, roc_curve as sk_roc

from metrics import stats


def _data(seed=1, n=300):
    rng = np.random.default_rng(seed)
    y = np.r_[np.ones(n // 2), np.zeros(n // 2)].astype(int)
    s = np.r_[rng.normal(1, 1, n // 2).round(1), rng.normal(0, 1, n // 2).round(1)]
    return y, s


def test_roc_and_auc_match_sklearn():
    y, s = _data()
    assert abs(stats.roc_auc(y, s) - roc_auc_score(y, s)) < 1e-12
    f, t, _ = stats.roc_curve(y, s)
    f2, t2, _ = sk_roc(y, s, drop_intermediate=False)
    assert abs(stats.auc(f, t) - np.trapezoid(t2, f2)) < 1e-12


def test_wilson_matches_scipy():
    for k, n in [(0, 50), (5, 100), (50, 100), (100, 100)]:
        ref = sps.binomtest(k, n).proportion_ci(0.95, method="wilson")
        lo, hi = stats.wilson_interval(k, n)
        assert abs(lo - ref.low) < 1e-9 and abs(hi - ref.high) < 1e-9


def test_confusion_and_rates():
    c = stats.confusion([1, 1, 0, 0, 1], [1, 0, 0, 1, 1])
    assert c == {"tp": 2, "fp": 1, "tn": 1, "fn": 1}
    r = stats.rates(c)
    assert abs(r["fp_rate"] - 0.5) < 1e-12 and abs(r["fn_rate"] - 1 / 3) < 1e-12


def test_choose_threshold_separable_gives_zero_error_and_margin():
    y = np.r_[np.ones(20), np.zeros(20)].astype(int)
    s = np.r_[np.linspace(100, 500, 20), np.linspace(0, 40, 20)]
    ch = stats.choose_threshold(y, s)
    assert ch["fp_rate"] == 0 and ch["fn_rate"] == 0
    assert 40 < ch["threshold"] < 100


def test_choose_threshold_respects_fp_budget():
    y, s = _data(5)
    ch = stats.choose_threshold(y, s, objective="error", max_fp_rate=0.05)
    assert ch["fp_rate"] <= 0.05


def test_mcnemar_exact_known_value():
    y = np.ones(12 + 20, dtype=int)
    a = np.r_[np.ones(10), np.zeros(2), np.ones(20)].astype(int)  # A right on 10 that B misses
    b = np.r_[np.zeros(10), np.ones(2), np.ones(20)].astype(int)
    r = stats.mcnemar_exact(y, a, b)
    assert (r["only_a_correct"], r["only_b_correct"]) == (10, 2)
    assert abs(r["p_value"] - 0.03857421875) < 1e-9


def test_bootstrap_ci_contains_estimate():
    y, s = _data(7)
    lo, hi = stats.bootstrap_ci(y, s, n_boot=200, seed=1)
    assert lo <= stats.roc_auc(y, s) <= hi
