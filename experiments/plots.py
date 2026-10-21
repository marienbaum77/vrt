"""Figures for the report (PNG, light surface, fixed categorical order, direct labels + legend).

Reads results/{scores.csv,summary.json,stability.json,performance.json}; writes results/figures/*.png.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from metrics import stats  # noqa: E402

from .common import GLOBAL_BASELINES, LABELS, LADDER, RESULTS, arr, read_csv  # noqa: E402

SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
S = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]  # categorical slots 1..8
TARGET = "#e34948"

plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE, "text.color": INK, "axes.labelcolor": INK2,
    "xtick.color": INK2, "ytick.color": INK2, "axes.edgecolor": GRID, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8,
    "axes.spines.top": False, "axes.spines.right": False, "font.size": 10, "axes.titlesize": 11, "axes.titleweight": "bold", "axes.titlelocation": "left",
    "legend.frameon": False, "figure.dpi": 100,
})
FIG = RESULTS / "figures"


def _save(fig, name: str) -> None:
    FIG.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIG / name, dpi=170, bbox_inches="tight")
    plt.close(fig)
    print("wrote", FIG / name)


def _rows():
    return read_csv(RESULTS / "scores.csv")


def roc(rows) -> None:
    test = [r for r in rows if r["split"] == "test" and r["tier"] == "core"]
    y = arr(test, "label").astype(int)
    fig, ax = plt.subplots(figsize=(5.6, 5.0))
    algs = [("pixel_tolerant", "VRT (calibrated)"), ("pixel_exact", "Exact pixel diff"), ("ssim_low", "SSIM (0.1 % pct.)"), ("phash", "pHash")]
    for i, (a, name) in enumerate(algs):
        fpr, tpr, _ = stats.roc_curve(y, arr(test, a))
        ax.plot(fpr, tpr, color=S[i], lw=2.2 if i == 0 else 1.8, label=f"{name}  AUC {stats.roc_auc(y, arr(test, a)):.3f}")
    ax.plot([0, 1], [0, 1], color=INK2, lw=1, ls=":")
    ax.axvline(0.05, color=TARGET, lw=1, ls="--")
    ax.axhline(0.95, color=TARGET, lw=1, ls="--")
    ax.text(0.06, 0.55, "FP ≤ 5 %", color=INK2, fontsize=8.5)
    ax.text(0.52, 0.935, "FN ≤ 5 %", color=INK2, fontsize=8.5)
    ax.set(xlabel="False-positive rate", ylabel="True-positive rate (1 − FN rate)", xlim=(-0.01, 1.01), ylim=(-0.01, 1.01), title="ROC on the held-out test split")
    ax.legend(loc="lower right", fontsize=8.5)
    _save(fig, "roc.png")


def operating_curve(rows, summary: Dict) -> None:
    T = summary["operating_point"]["fail_pixels"]
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.8), sharey=True)
    for ax, split in zip(axes, ("calibration", "test")):
        rr = [r for r in rows if r["split"] == split and r["tier"] == "core"]
        sw = stats.sweep(arr(rr, "label").astype(int), arr(rr, "pixel_tolerant"))
        t = sw["threshold"]
        ok = np.isfinite(t) & (t > 0)
        ax.plot(t[ok], sw["fp_rate"][ok] * 100, color=S[0], lw=2, label="FP rate")
        ax.plot(t[ok], sw["fn_rate"][ok] * 100, color=S[1], lw=2, label="FN rate")
        ax.axhline(5, color=TARGET, lw=1, ls="--")
        ax.axvline(T, color=INK2, lw=1, ls=":")
        ax.set_xscale("log")
        ax.set(title=f"{split} split", xlabel="fail_pixels (log)")
        ax.text(T * 1.08, 62, f"chosen\n{T:.0f} px", color=INK2, fontsize=8.5)
    axes[0].set_ylabel("error rate, %")
    axes[0].legend(loc="upper center", fontsize=9)
    axes[0].text(axes[0].get_xlim()[0] * 1.2, 5.8, "5 % target", color=TARGET, fontsize=8.5)
    _save(fig, "fp_fn_vs_threshold.png")


def ladder(summary: Dict) -> None:
    algs = LADDER + GLOBAL_BASELINES
    fig, ax = plt.subplots(figsize=(8.2, 4.6))
    y = np.arange(len(algs))
    fp = [summary["algorithms"][a]["fp_rate"] * 100 for a in algs]
    fn = [summary["algorithms"][a]["fn_rate"] * 100 for a in algs]
    ax.barh(y - 0.19, fp, 0.36, color=S[0], label="FP rate")
    ax.barh(y + 0.19, fn, 0.36, color=S[1], label="FN rate")
    for yi, (a, b) in enumerate(zip(fp, fn)):
        ax.text(a + 0.6, yi - 0.19, f"{a:.1f}", va="center", fontsize=8, color=INK2)
        ax.text(b + 0.6, yi + 0.19, f"{b:.1f}", va="center", fontsize=8, color=INK2)
    ax.axvline(5, color=TARGET, lw=1, ls="--")
    ax.set_yticks(y, [LABELS[a] for a in algs])
    ax.invert_yaxis()
    ax.set(xlabel="error rate on test split, % (threshold calibrated per algorithm)", title="Tolerance ladder and comparison algorithms")
    ax.legend(loc="lower right")
    ax.grid(axis="y", visible=False)
    _save(fig, "algorithms.png")


def categories(summary: Dict) -> None:
    rows = [r for r in summary["per_category"] if r["tier"] == "core" and "+" not in r["category"]]
    rows.sort(key=lambda r: (r["label"], -r["error_rate"]))
    fig, ax = plt.subplots(figsize=(8.2, 0.26 * len(rows) + 1.2))
    y = np.arange(len(rows))
    col = [S[1] if r["label"] else S[0] for r in rows]
    ax.barh(y, [r["error_rate"] * 100 for r in rows], 0.62, color=col)
    ax.errorbar([r["error_rate"] * 100 for r in rows], y, xerr=[[max(0.0, r["error_rate"] - r["ci"][0]) * 100 for r in rows], [max(0.0, r["ci"][1] - r["error_rate"]) * 100 for r in rows]],
                fmt="none", ecolor=INK2, lw=0.8, capsize=2)
    ax.set_yticks(y, [f"{'changed' if r['label'] else 'unchanged'} · {r['category']} (n={r['n']})" for r in rows], fontsize=8)
    ax.invert_yaxis()
    ax.axvline(5, color=TARGET, lw=1, ls="--")
    ax.set(xlabel="error rate, % (95 % Wilson interval)", title="Errors by category (calibration + test, core tier)")
    ax.grid(axis="y", visible=False)
    _save(fig, "per_category.png")


def sensitivity(summary: Dict) -> None:
    curves = summary["sensitivity_curves"]
    fig, axes = plt.subplots(1, len(curves), figsize=(3.1 * len(curves), 3.2), sharey=True)
    for ax, (kind, d), c in zip(np.atleast_1d(axes), curves.items(), S):
        x = [p["magnitude"] for p in d["points"]]
        r = np.array([p["rate"] for p in d["points"]]) * 100
        lo = np.array([p["ci"][0] for p in d["points"]]) * 100
        hi = np.array([p["ci"][1] for p in d["points"]]) * 100
        ax.fill_between(x, lo, hi, color=c, alpha=0.18, lw=0)
        ax.plot(x, r, color=c, lw=2, marker="o", ms=4)
        ax.axhline(95, color=TARGET, lw=1, ls="--")
        ax.set(title=kind, xlabel=d["unit"])
    np.atleast_1d(axes)[0].set_ylabel("regressions detected, %")
    fig.suptitle("Detection limits: smallest visible regression the calibrated thresholds catch", x=0.01, ha="left", fontsize=11, fontweight="bold")
    _save(fig, "sensitivity.png")


def holdout(summary: Dict) -> None:
    kinds = [k for k in summary["holdout"] if k != "all"]
    fig, ax = plt.subplots(figsize=(7.4, 3.6))
    x = np.arange(len(kinds))
    fp = [summary["holdout"][k]["fp_rate"] * 100 for k in kinds]
    rc = [summary["holdout"][k]["recall"] * 100 for k in kinds]
    ax.bar(x - 0.19, fp, 0.36, color=S[0], label="false positives (noise only)")
    ax.bar(x + 0.19, [100 - v for v in rc], 0.36, color=S[1], label="missed regressions (FN)")
    ax.axhline(5, color=TARGET, lw=1, ls="--")
    labels = [f"{k}\n{'seen' if summary['holdout'][k].get('seen_in_calibration') else 'unseen'}" for k in kinds]
    ax.set_xticks(x, labels)
    ax.set(ylabel="error rate, %", title="Image-space noise on the test scenes")
    ax.legend(fontsize=8.5)
    ax.grid(axis="x", visible=False)
    _save(fig, "holdout_noise.png")


def stability() -> None:
    p = RESULTS / "stability.json"
    if not p.exists():
        return
    d = json.loads(p.read_text())
    modes = list(d["modes"])
    kinds = list(next(iter(d["modes"].values()))["per_kind"])
    fig, ax = plt.subplots(figsize=(8.4, 3.8))
    w = 0.8 / len(modes)
    for i, m in enumerate(modes):
        v = [d["modes"][m]["per_kind"][k]["fp_rate"] * 100 for k in kinds]
        ax.bar(np.arange(len(kinds)) + (i - (len(modes) - 1) / 2) * w, v, w * 0.92, color=S[i], label=m)
    ax.set_xticks(np.arange(len(kinds)), [k.replace("_", "\n") for k in kinds])
    ax.set(ylabel="false positives, % of identical re-captures", title="What capture stabilisation removes")
    ax.legend(ncol=len(modes), fontsize=8.5, loc="upper right")
    ax.grid(axis="x", visible=False)
    _save(fig, "stability.png")


def performance() -> None:
    p = RESULTS / "performance.json"
    if not p.exists():
        return
    d = json.loads(p.read_text())
    res, lv = d["resolution"], d["service"]["levels"]
    fig, axes = plt.subplots(1, 3, figsize=(12, 3.5))
    mp = [r["megapixels"] for r in res]
    axes[0].plot(mp, [r["median_ms"] for r in res], color=S[0], lw=2, marker="o")
    axes[0].set(title="Comparison time", xlabel="megapixels", ylabel="median ms")
    axes[1].plot(mp, [r["peak_traced_mb"] for r in res], color=S[1], lw=2, marker="o")
    axes[1].set(title="Peak memory (tracemalloc)", xlabel="megapixels", ylabel="MB")
    c = [l["concurrency"] for l in lv]
    axes[2].plot(c, [l["p50_ms"] for l in lv], color=S[0], lw=2, marker="o", label="p50")
    axes[2].plot(c, [l["p95_ms"] for l in lv], color=S[1], lw=2, marker="o", label="p95")
    axes[2].set(title="API latency, 1280×720", xlabel="concurrent clients", ylabel="ms", xscale="log", xticks=c, xticklabels=c)
    axes[2].legend(fontsize=8.5)
    _save(fig, "performance.png")


def make_all() -> None:
    rows = _rows()
    summary = json.loads((RESULTS / "summary.json").read_text())
    roc(rows)
    operating_curve(rows, summary)
    ladder(summary)
    categories(summary)
    sensitivity(summary)
    holdout(summary)
    stability()
    performance()


if __name__ == "__main__":
    make_all()
