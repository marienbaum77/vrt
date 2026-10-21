"""Statistics for the verification stage: confusion matrix, FP/FN rates, intervals, ROC, tests.

Everything here is plain NumPy/SciPy and is cross-checked against scikit-learn / statsmodels-free
reference values in ``tests/test_stats.py``.

Convention used throughout the project
--------------------------------------
* label 1 = "changed"   (a real UI regression, the *positive* class)
* label 0 = "unchanged" (identical or only rendering noise / masked dynamic content)
* a *score* is "how different are the screenshots" (higher = more different)
* a comparison predicts "changed" when ``score >= threshold``
* FP rate = FP / (FP + TN)   -> share of unchanged pairs that wrongly fail the build
* FN rate = FN / (FN + TP)   -> share of real regressions that slip through
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, Optional, Sequence, Tuple

import numpy as np
from scipy import stats as sps


# ----------------------------------------------------------------------------- confusion
def confusion(y_true: Sequence[int], y_pred: Sequence[int]) -> Dict[str, int]:
    yt = np.asarray(y_true, dtype=int)
    yp = np.asarray(y_pred, dtype=int)
    return {
        "tp": int(((yt == 1) & (yp == 1)).sum()),
        "fp": int(((yt == 0) & (yp == 1)).sum()),
        "tn": int(((yt == 0) & (yp == 0)).sum()),
        "fn": int(((yt == 1) & (yp == 0)).sum()),
    }


def _div(a: float, b: float) -> float:
    return float(a) / float(b) if b else 0.0


def rates(c: Dict[str, int]) -> Dict[str, float]:
    tp, fp, tn, fn = c["tp"], c["fp"], c["tn"], c["fn"]
    precision = _div(tp, tp + fp)
    recall = _div(tp, tp + fn)
    return {
        "accuracy": _div(tp + tn, tp + fp + tn + fn),
        "fp_rate": _div(fp, fp + tn),
        "fn_rate": _div(fn, fn + tp),
        "precision": precision,
        "recall": recall,
        "f1": _div(2 * precision * recall, precision + recall),
        "balanced_accuracy": 0.5 * (_div(tn, tn + fp) + recall),
    }


def wilson_interval(k: int, n: int, confidence: float = 0.95) -> Tuple[float, float]:
    """Wilson score interval for a binomial proportion (well-behaved for p near 0 and small n)."""
    if n <= 0:
        return (0.0, 1.0)
    z = float(sps.norm.ppf(0.5 + confidence / 2.0))
    p = k / n
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, float(centre - half)), min(1.0, float(centre + half)))


def clopper_pearson(k: int, n: int, confidence: float = 0.95) -> Tuple[float, float]:
    """Exact (conservative) binomial interval."""
    if n <= 0:
        return (0.0, 1.0)
    a = 1.0 - confidence
    lo = 0.0 if k == 0 else float(sps.beta.ppf(a / 2, k, n - k + 1))
    hi = 1.0 if k == n else float(sps.beta.ppf(1 - a / 2, k + 1, n - k))
    return (lo, hi)


def evaluate(y_true: Sequence[int], y_pred: Sequence[int], confidence: float = 0.95) -> Dict[str, object]:
    """Confusion matrix, rates and Wilson intervals for FP/FN/accuracy."""
    c = confusion(y_true, y_pred)
    r = rates(c)
    n_neg, n_pos = c["fp"] + c["tn"], c["fn"] + c["tp"]
    out: Dict[str, object] = {**c, **r, "n": n_neg + n_pos, "n_neg": n_neg, "n_pos": n_pos}
    out["fp_rate_ci"] = wilson_interval(c["fp"], n_neg, confidence)
    out["fn_rate_ci"] = wilson_interval(c["fn"], n_pos, confidence)
    out["accuracy_ci"] = wilson_interval(c["tp"] + c["tn"], n_pos + n_neg, confidence)
    return out


# ----------------------------------------------------------------------------------- ROC
def roc_curve(y_true: Sequence[int], scores: Sequence[float]) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """ROC points for the rule ``score >= threshold`` (ties handled; starts at (0,0), ends at (1,1)).

    Returns ``(fpr, tpr, thresholds)``; ``thresholds[0] = +inf`` corresponds to the (0,0) point.
    """
    yt = np.asarray(y_true, dtype=int)
    sc = np.asarray(scores, dtype=float)
    order = np.argsort(-sc, kind="mergesort")
    sc, yt = sc[order], yt[order]
    distinct = np.where(np.diff(sc))[0]
    idx = np.r_[distinct, yt.size - 1]
    tps = np.cumsum(yt)[idx]
    fps = 1 + idx - tps
    P, N = int(yt.sum()), int((1 - yt).sum())
    tpr = tps / P if P else np.zeros_like(tps, dtype=float)
    fpr = fps / N if N else np.zeros_like(fps, dtype=float)
    return np.r_[0.0, fpr], np.r_[0.0, tpr], np.r_[np.inf, sc[idx]]


def auc(fpr: np.ndarray, tpr: np.ndarray) -> float:
    return float(np.trapezoid(tpr, fpr))


def roc_auc(y_true: Sequence[int], scores: Sequence[float]) -> float:
    fpr, tpr, _ = roc_curve(y_true, scores)
    return auc(fpr, tpr)


def bootstrap_ci(
    y_true: Sequence[int],
    scores: Sequence[float],
    fn: Callable[[np.ndarray, np.ndarray], float] = roc_auc,
    n_boot: int = 1000,
    confidence: float = 0.95,
    seed: int = 0,
) -> Tuple[float, float]:
    """Stratified percentile bootstrap of any metric ``fn(y_true, scores)`` (default: ROC AUC)."""
    rng = np.random.default_rng(seed)
    yt = np.asarray(y_true, dtype=int)
    sc = np.asarray(scores, dtype=float)
    pos, neg = np.flatnonzero(yt == 1), np.flatnonzero(yt == 0)
    vals = []
    for _ in range(n_boot):
        i = np.r_[rng.choice(pos, pos.size), rng.choice(neg, neg.size)]
        vals.append(fn(yt[i], sc[i]))
    a = (1 - confidence) / 2
    return float(np.quantile(vals, a)), float(np.quantile(vals, 1 - a))


# --------------------------------------------------------------------- threshold selection
def _mid(lo: float, hi: float, log: bool) -> float:
    if np.isinf(lo):
        return hi / 2 if hi > 0 else hi - 1
    if np.isinf(hi):
        return lo * 2 if lo > 0 else lo + 1
    if log and lo >= 0:
        return float(np.sqrt((lo + 1.0) * (hi + 1.0)) - 1.0)
    return float((lo + hi) / 2.0)


def sweep(y_true: Sequence[int], scores: Sequence[float], log_scale: bool = True) -> Dict[str, np.ndarray]:
    """FP rate and FN rate for every distinct decision boundary (midpoints between sorted scores)."""
    yt = np.asarray(y_true, dtype=int)
    sc = np.asarray(scores, dtype=float)
    u = np.unique(sc)
    edges = np.r_[-np.inf, u, np.inf]
    cand = np.array([_mid(edges[i], edges[i + 1], log_scale) for i in range(len(edges) - 1)])
    # keep boundary strictly between neighbours (ties broken towards 'not changed' below the threshold)
    pos, neg = sc[yt == 1], sc[yt == 0]
    fn = np.array([(pos < t).sum() for t in cand], dtype=float) / max(len(pos), 1)
    fp = np.array([(neg >= t).sum() for t in cand], dtype=float) / max(len(neg), 1)
    return {"threshold": cand, "fp_rate": fp, "fn_rate": fn}


def choose_threshold(
    y_true: Sequence[int],
    scores: Sequence[float],
    objective: str = "minimax",
    max_fp_rate: Optional[float] = None,
    log_scale: bool = True,
) -> Dict[str, float]:
    """Pick the decision threshold on a *calibration* sample.

    objective
        ``minimax``  minimise max(FP rate, FN rate)  (default; directly targets "both <= 5 %")
        ``youden``   maximise TPR - FPR
        ``error``    minimise FP rate + FN rate
    The optimum is usually a plateau; the centre of the widest plateau is returned (max margin).
    ``max_fp_rate`` optionally restricts the search to thresholds respecting a false-alarm budget.
    """
    s = sweep(y_true, scores, log_scale)
    t, fp, fn = s["threshold"], s["fp_rate"], s["fn_rate"]
    if objective == "minimax":
        cost = np.maximum(fp, fn)
    elif objective == "youden":
        cost = (fp + fn) / 2.0
    elif objective == "error":
        cost = fp + fn
    else:
        raise ValueError(objective)
    if max_fp_rate is not None:
        cost = np.where(fp <= max_fp_rate, cost, np.inf)
    best = cost.min()
    ok = np.flatnonzero(np.isclose(cost, best, atol=1e-12))
    # group consecutive candidates -> plateaus; pick widest in score space
    groups, cur = [], [ok[0]]
    for i in ok[1:]:
        if i == cur[-1] + 1:
            cur.append(i)
        else:
            groups.append(cur)
            cur = [i]
    groups.append(cur)
    g = max(groups, key=lambda grp: (t[grp[-1]] - t[grp[0]]) if np.isfinite(t[grp[-1]] - t[grp[0]]) else 0.0)
    thr = float(t[g[len(g) // 2]])
    return {"threshold": thr, "fp_rate": float(fp[g[len(g) // 2]]), "fn_rate": float(fn[g[len(g) // 2]]), "cost": float(best)}


def predict(scores: Sequence[float], threshold: float) -> np.ndarray:
    return (np.asarray(scores, dtype=float) >= threshold).astype(int)


# ------------------------------------------------------------------------------- testing
def mcnemar_exact(y_true: Sequence[int], pred_a: Sequence[int], pred_b: Sequence[int]) -> Dict[str, float]:
    """Exact two-sided McNemar test on paired correctness of two classifiers.

    ``b`` = pairs only A classifies correctly, ``c`` = pairs only B classifies correctly.
    H0: both have the same error rate.
    """
    yt = np.asarray(y_true)
    ca = np.asarray(pred_a) == yt
    cb = np.asarray(pred_b) == yt
    b = int((ca & ~cb).sum())
    c = int((~ca & cb).sum())
    n = b + c
    p = 1.0 if n == 0 else float(min(1.0, 2.0 * sps.binom.cdf(min(b, c), n, 0.5)))
    return {"only_a_correct": b, "only_b_correct": c, "p_value": p}


def fisher_exact(a: int, b: int, c: int, d: int) -> float:
    return float(sps.fisher_exact([[a, b], [c, d]])[1])
