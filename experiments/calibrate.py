"""Threshold calibration on the *calibration* split (core tier only).

1. grid search over (geometry radius, blur sigma, blur dE tolerance, region merge / min area)
   with the decision threshold ``fail_pixels`` chosen per configuration by the minimax rule
   (minimise max(FP rate, FN rate));
2. select the configuration with the lowest calibration cost (ties -> higher AUC -> fewer knobs);
3. estimate the optimism of that selection with 5-fold cross-validation *inside* the calibration split.

The calibration pairs are the rendered-scene pairs of the calibration scenes plus image-space noise pairs
(JPEG, Gaussian, resample, gamma) built from those same scenes. The test split and the WebP/blur families are never read here.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np

from metrics import stats

from .common import DATA, RESULTS, load_manifest
from .grid import CONFIGS, run_grid


def _best(y: np.ndarray, mat: np.ndarray) -> Tuple[int, Dict[str, float], float]:
    """Index of the best config, its threshold choice and AUC. Ties: lower cost, higher AUC, larger min_area (smoother)."""
    best: Tuple[Any, ...] = (9.0, 0.0, 0, -1, {})
    for j in range(mat.shape[1]):
        s = mat[:, j]
        ch = stats.choose_threshold(y, s, "minimax")
        key = (round(ch["cost"], 9), -stats.roc_auc(y, s), CONFIGS[j]["merge_px"], j)
        if key < best[:4]:
            best = (*key, ch)
    j = best[3]
    return j, best[4], -best[1]


def calibrate(workers: int = 2, out: Path = RESULTS / "calibration.json", cache: Path = RESULTS / "grid_calibration.npz", reuse: bool = False) -> Dict[str, Any]:
    man = load_manifest()
    pairs = [p for p in man["pairs"] if p["split"] == "calibration" and p["tier"] == "core"]
    pairs += load_manifest("calibration_noise_manifest.json")["pairs"]  # image-space noise on calibration scenes
    if reuse and cache.exists():
        z = np.load(cache)
        ids, mat = list(z["ids"]), z["mat"]
    else:
        ids, mat = run_grid(DATA, pairs, workers)
        cache.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(cache, ids=np.array(ids), mat=mat)
    byid = {p["id"]: p for p in pairs}
    y = np.array([byid[i]["label"] for i in ids])

    j, ch, auc = _best(y, mat)
    cfg = dict(CONFIGS[j])
    s = mat[:, j]

    # 5-fold CV inside the calibration split: selection + threshold on 4 folds, error on the 5th
    rng = np.random.default_rng(0)
    order = rng.permutation(len(y))
    folds = np.array_split(order, 5)
    cv_fp, cv_fn, cv_acc = [], [], []
    for k in range(5):
        te = folds[k]
        tr = np.concatenate([folds[i] for i in range(5) if i != k])
        jk, chk, _ = _best(y[tr], mat[tr])
        pred = stats.predict(mat[te, jk], chk["threshold"])
        r = stats.rates(stats.confusion(y[te], pred))
        cv_fp.append(r["fp_rate"]), cv_fn.append(r["fn_rate"]), cv_acc.append(r["accuracy"])

    # how flat is the optimum? number of grid configs within 1 pp of the best cost
    costs = np.array([stats.choose_threshold(y, mat[:, i], "minimax")["cost"] for i in range(mat.shape[1])])
    result = {
        "n_pairs": int(len(y)), "n_positive": int(y.sum()),
        "config": cfg,
        "fail_pixels": ch["threshold"],
        "calibration": {"fp_rate": ch["fp_rate"], "fn_rate": ch["fn_rate"], "auc": auc},
        "cross_validation_5fold": {"fp_rate_mean": float(np.mean(cv_fp)), "fn_rate_mean": float(np.mean(cv_fn)),
                                   "accuracy_mean": float(np.mean(cv_acc)), "accuracy_sd": float(np.std(cv_acc))},
        "grid_size": len(CONFIGS),
        "configs_within_1pp_of_best": int((costs <= costs.min() + 0.01).sum()),
        "objective": "minimax: minimise max(FP rate, FN rate) on the calibration split",
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
    return result
