"""Evaluation of the calibrated operating point and of every comparison algorithm on held-out data.

Reads ``results/calibration.json`` (made from the calibration split only) and writes
``results/scores.csv``, ``results/summary.json``, ``results/RESULTS.md`` and the Prometheus / JSON metric exports.
"""
from __future__ import annotations

import json
import time
from collections import defaultdict
from multiprocessing import Pool
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

import numpy as np

from diff_engine import DiffConfig
from metrics import Metrics, stats

from .common import DATA, GLOBAL_BASELINES, LABELS, LADDER, RESULTS, arr, calibrated_config, load_manifest, pct, read_csv, score_pairs, write_csv
from .features import extract, significant_pixels

ALGORITHMS = LADDER + GLOBAL_BASELINES


def _rows(rows, split=None, tier="core", label=None):
    out = [r for r in rows if (split is None or r["split"] == split) and (tier is None or r["tier"] == tier)]
    return [r for r in out if label is None or r["label"] == label]


def _ev(y, s, thr) -> Dict[str, Any]:
    e = stats.evaluate(y, stats.predict(s, thr))
    e["threshold"] = float(thr)
    e["auc"] = stats.roc_auc(y, s)
    e["auc_ci"] = stats.bootstrap_ci(y, s, n_boot=500, seed=1)
    return e


def _clean(o: Any) -> Any:
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    return o


# --------------------------------------------------------------------------- algorithms
def compare_algorithms(rows) -> Dict[str, Any]:
    """Calibrate each scorer's threshold on calibration/core, report on test/core."""
    cal, test = _rows(rows, "calibration"), _rows(rows, "test")
    yc, yt = arr(cal, "label").astype(int), arr(test, "label").astype(int)
    out: Dict[str, Any] = {}
    for alg in ALGORITHMS:
        sc, st = arr(cal, alg), arr(test, alg)
        ch = stats.choose_threshold(yc, sc, "minimax")
        e = _ev(yt, st, ch["threshold"])
        e["calibration"] = {"fp_rate": ch["fp_rate"], "fn_rate": ch["fn_rate"], "cost": ch["cost"]}
        e["label"] = LABELS[alg]
        out[alg] = e
    return out


# -------------------------------------------------------------------------- categories
def per_category(rows, thr: float, alg: str = "pixel_tolerant") -> List[Dict[str, Any]]:
    by: Dict[Tuple[int, str, str], List[float]] = defaultdict(list)
    for r in rows:
        if r["split"] in ("calibration", "test"):
            by[(r["label"], r["category"], r["tier"])].append(r[alg])
    out = []
    for (label, cat, tier), v in sorted(by.items()):
        v = np.array(v)
        wrong = int((v >= thr).sum()) if label == 0 else int((v < thr).sum())
        lo, hi = stats.wilson_interval(wrong, len(v))
        out.append({"label": label, "category": cat, "tier": tier, "n": len(v), "errors": wrong, "error_rate": wrong / len(v), "ci": [lo, hi]})
    return out


# --------------------------------------------------------------------------- sensitivity
def _sens_work(args) -> Dict[str, int]:
    p, cal = args
    c = cal["config"]
    base = {"radius": c["radius_px"], "sigma": c["blur_sigma_px"], "tol": c["blur_delta_e"], "merge": c["merge_px"], "area": c["min_area_px"], "color": 2.3}
    cache: Dict[Tuple, Any] = {}

    def sus(radius, sigma, color):
        k = (radius, round(sigma, 4), round(color, 4))
        if k not in cache:
            cache[k] = extract(DATA / p["baseline"], DATA / p["candidate"], p["regions"], radii=[radius], sigmas=[sigma], color_delta_e=color)[0][(radius, sigma)]
        return cache[k]

    def count(radius=base["radius"], sigma=base["sigma"], color=base["color"], tol=base["tol"], merge=base["merge"], area=base["area"]):
        ys, xs, d = sus(radius, sigma, color)
        return significant_pixels(ys, xs, d, tol, merge, area)

    out = {"base": count()}
    for name, kw in {
        "color_delta_e+10%": dict(color=base["color"] * 1.1), "color_delta_e-10%": dict(color=base["color"] * 0.9),
        "blur_sigma_px+10%": dict(sigma=base["sigma"] * 1.1), "blur_sigma_px-10%": dict(sigma=base["sigma"] * 0.9),
        "blur_delta_e+10%": dict(tol=base["tol"] * 1.1), "blur_delta_e-10%": dict(tol=base["tol"] * 0.9),
        "radius_px+1": dict(radius=base["radius"] + 1), "radius_px-1": dict(radius=max(0, base["radius"] - 1)),
        "merge_px+1": dict(merge=base["merge"] + 1), "merge_px-1": dict(merge=max(0, base["merge"] - 1)),
        "min_area_px+1": dict(area=base["area"] + 1), "min_area_px-1": dict(area=max(1, base["area"] - 1)),
    }.items():
        out[name] = count(**kw)
    return out


def threshold_sensitivity(test_pairs, cal, workers: int = 2) -> Dict[str, Any]:
    """Share of test verdicts that flip when one parameter moves by +/-10 % (+/-1 for integer parameters)."""
    with Pool(workers) as pool:
        res = pool.map(_sens_work, [(p, cal) for p in test_pairs], chunksize=2)
    T = cal["fail_pixels"]
    y = np.array([p["label"] for p in test_pairs])
    v0 = np.array([r["base"] >= T for r in res])
    out: Dict[str, Any] = {}
    names = [k for k in res[0] if k != "base"]
    for n in names:
        v = np.array([r[n] >= T for r in res])
        pred = v.astype(int)
        e = stats.rates(stats.confusion(y, pred))
        out[n] = {"flip_rate": float((v != v0).mean()), "fp_rate": e["fp_rate"], "fn_rate": e["fn_rate"]}
    for f, name in ((1.1, "fail_pixels+10%"), (0.9, "fail_pixels-10%")):
        v = np.array([r["base"] >= T * f for r in res])
        e = stats.rates(stats.confusion(y, v.astype(int)))
        out[name] = {"flip_rate": float((v != v0).mean()), "fp_rate": e["fp_rate"], "fn_rate": e["fn_rate"]}
    return out


# --------------------------------------------------------------------------- main entry
def analyze(workers: int = 2, reuse_scores: bool = False) -> Dict[str, Any]:
    t0 = time.time()
    cal = json.loads((RESULTS / "calibration.json").read_text())
    cfg = calibrated_config(cal)
    man = load_manifest()
    hold = load_manifest("holdout_manifest.json")
    calnoise = load_manifest("calibration_noise_manifest.json")
    sens = load_manifest("sensitivity_manifest.json")

    if reuse_scores and (RESULTS / "scores.csv").exists():
        rows = read_csv(RESULTS / "scores.csv")
    else:
        print("scoring main dataset + holdout ...")
        rows = score_pairs(man["pairs"] + calnoise["pairs"] + hold["pairs"], cfg, DATA, workers)
        rows.sort(key=lambda r: r["id"])
        write_csv(rows, RESULTS / "scores.csv")

    algs = compare_algorithms([r for r in rows if r["split"] != "holdout"])
    final = algs["pixel_tolerant"]
    T = final["threshold"]
    if abs(T - cal["fail_pixels"]) > 1e-3 * max(T, 1):
        print(f"warning: per-scorer threshold {T} differs from calibration.json {cal['fail_pixels']}")

    core_all = [r for r in rows if r["split"] in ("calibration", "test") and r["tier"] == "core"]
    test = _rows(rows, "test")
    yt = arr(test, "label").astype(int)
    fpr_c, tpr_c, thr_c = stats.roc_curve(yt, arr(test, "pixel_tolerant"))

    # operating curve (FP/FN vs fail_pixels) on both splits
    sweeps = {}
    for split in ("calibration", "test"):
        rr = _rows(rows, split)
        sweeps[split] = {k: v.tolist() for k, v in stats.sweep(arr(rr, "label").astype(int), arr(rr, "pixel_tolerant")).items()}

    # stress tier: "unchanged" pairs whose layout drifted >= 1 px (not part of the headline metric)
    stress = [r for r in rows if r["tier"] == "stress" and r["split"] in ("calibration", "test")]
    stress_fp = int(sum(r["pixel_tolerant"] >= T for r in stress))
    stress_by: Dict[str, List[int]] = defaultdict(list)
    for r in stress:
        stress_by[r["category"]].append(int(r["pixel_tolerant"] >= T))

    # holdout noise families (never used for calibration)
    hrows = [r for r in rows if r["split"] == "holdout"]
    hold_out: Dict[str, Any] = {}
    for kind in ("jpeg", "gauss", "resample", "gamma", "webp", "blur"):
        negs = [r for r in hrows if r["label"] == 0 and r["category"] == kind]
        poss = [r for r in hrows if r["label"] == 1 and r["category"].endswith("+" + kind)]
        fp = sum(r["pixel_tolerant"] >= T for r in negs)
        tp = sum(r["pixel_tolerant"] >= T for r in poss)
        hold_out[kind] = {"seen_in_calibration": kind in ("jpeg", "gauss", "resample", "gamma"), "n_neg": len(negs), "false_positives": int(fp), "fp_rate": fp / len(negs), "fp_ci": stats.wilson_interval(int(fp), len(negs)),
                          "n_pos": len(poss), "detected": int(tp), "recall": tp / max(len(poss), 1)}
    hn = [r for r in hrows if r["label"] == 0]
    hp = [r for r in hrows if r["label"] == 1]
    hold_out["all"] = {"fp_rate": sum(r["pixel_tolerant"] >= T for r in hn) / len(hn), "fn_rate": sum(r["pixel_tolerant"] < T for r in hp) / len(hp),
                       "n_neg": len(hn), "n_pos": len(hp),
                       "fp_ci": stats.wilson_interval(int(sum(r["pixel_tolerant"] >= T for r in hn)), len(hn)),
                       "fn_ci": stats.wilson_interval(int(sum(r["pixel_tolerant"] < T for r in hp)), len(hp))}

    # paired tests on the test split: calibrated VRT vs (a) the best global-similarity baseline, (b) the ladder without the anti-aliasing stage
    p_vrt = stats.predict(arr(test, "pixel_tolerant"), T)
    mcn = {}
    best_global = max(GLOBAL_BASELINES, key=lambda a: algs[a]["accuracy"])
    for name, other in (("best_global_baseline", best_global), ("without_antialiasing_stage", "pixel_geometry")):
        p_oth = stats.predict(arr(test, other), algs[other]["threshold"])
        m = stats.mcnemar_exact(yt, p_vrt, p_oth)
        m["against"] = other
        mcn[name] = m

    # detection limits (sensitivity sweep, clean mutations of known size)
    print("sensitivity sweep ...")
    scfg = DiffConfig.from_dict({**cfg.to_dict(), "extras": []})
    srows = score_pairs([{**p, "label": 1, "category": p["kind"], "split": "sensitivity"} for p in sens["pairs"]], scfg, DATA, workers)
    byid = {p["id"]: p for p in sens["pairs"]}
    curve: Dict[str, Dict[str, Any]] = defaultdict(dict)
    for r in srows:
        p = byid[r["id"]]
        curve[p["kind"]].setdefault(str(p["magnitude"]), []).append(int(r["pixel_tolerant"] >= T))
    sens_out = {}
    for kind, d in curve.items():
        sens_out[kind] = {"unit": sens["grid"] and next(q["unit"] for q in sens["pairs"] if q["kind"] == kind),
                          "points": [{"magnitude": float(m), "n": len(v), "detected": sum(v), "rate": sum(v) / len(v), "ci": stats.wilson_interval(sum(v), len(v))}
                                     for m, v in sorted(d.items(), key=lambda kv: float(kv[0]))]}

    print("threshold sensitivity ...")
    tsens = threshold_sensitivity([p for p in man["pairs"] if p["split"] == "test" and p["tier"] == "core"], cal, workers)

    # timing of the production comparison (1280x720, calibrated config with extras off is faster; this is with extras)
    times = arr([r for r in rows if r["split"] != "holdout"], "time_ms")

    # observability export
    mx = Metrics()
    for a in ALGORITHMS:
        mx.set_error_rates(a, "test", algs[a]["fp_rate"], algs[a]["fn_rate"])
    mx.set_error_rates("vrt_calibrated", "holdout", hold_out["all"]["fp_rate"], hold_out["all"]["fn_rate"])
    for k, v in tsens.items():
        if k.endswith("+10%") or k.endswith("+1"):
            mx.set_threshold_sensitivity(k.rsplit("+", 1)[0], v["flip_rate"])
    for r in rows:
        if r["split"] != "holdout":
            mx.observe_comparison(result="failed" if r["pixel_tolerant"] >= T else "passed", time_ms=r["time_ms"], diff_pixels=int(r["diff_pixels"]), algorithm="vrt_calibrated")
    mx.export_prometheus(RESULTS / "metrics.prom")
    mx.export_json(RESULTS / "metrics.json")

    summary = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "dataset": {"name": man["name"], "sha256": man["content_sha256"], "seed": man["seed"], "counts": man["counts"], "labelling": man.get("labelling")},
        "calibration": cal,
        "operating_point": {"fail_pixels": T, "config": cal["config"]},
        "algorithms": algs,
        "roc_vrt": {"fpr": fpr_c.tolist(), "tpr": tpr_c.tolist()},
        "sweeps": sweeps,
        "per_category": per_category(rows, T),
        "stress": {"n": len(stress), "false_positives": stress_fp, "fp_rate": stress_fp / max(len(stress), 1), "ci": stats.wilson_interval(stress_fp, max(len(stress), 1)),
                   "by_category": {k: {"n": len(v), "false_positives": sum(v)} for k, v in stress_by.items()}},
        "holdout": hold_out,
        "mcnemar": mcn,
        "sensitivity_curves": sens_out,
        "threshold_sensitivity": tsens,
        "latency_ms": {"p50": float(np.percentile(times, 50)), "p95": float(np.percentile(times, 95)), "mean": float(times.mean())},
        "runtime_s": round(time.time() - t0, 1),
    }
    (RESULTS / "summary.json").write_text(json.dumps(_clean(summary), indent=2))
    write_markdown(_clean(summary))
    print(f"analysis done in {time.time() - t0:.0f}s")
    return summary


def write_markdown(s: Dict[str, Any]) -> None:
    L: List[str] = []
    c = s["dataset"]["counts"]
    op = s["operating_point"]
    L += ["# Verification results", "",
          f"Dataset `{s['dataset']['name']}` (seed {s['dataset']['seed']}, sha256 `{s['dataset']['sha256'][:16]}…`): {c['pairs']} pairs, "
          f"{c['core']} core + {c['stress']} stress. Thresholds are calibrated on the calibration split only; all numbers below are on the held-out **test split, core tier** "
          f"unless stated otherwise. Intervals are 95 % Wilson (rates) / bootstrap (AUC).", "",
          f"**Operating point:** `radius_px={op['config']['radius_px']}`, `blur_sigma_px={op['config']['blur_sigma_px']}`, `blur ΔE={op['config']['blur_delta_e']}`, "
          f"`merge_px={op['config']['merge_px']}`, `min_area_px={op['config']['min_area_px']}`, `fail_pixels={op['fail_pixels']:.1f}`.", "",
          "## Accuracy of the compared algorithms (test split)", "",
          "| algorithm | accuracy | FP rate | FN rate | AUC | threshold |", "|---|---|---|---|---|---|"]
    for a, e in s["algorithms"].items():
        L.append(f"| {e['label']} | {pct(e['accuracy'])} [{pct(e['accuracy_ci'][0])}–{pct(e['accuracy_ci'][1])}] | {pct(e['fp_rate'])} [{pct(e['fp_rate_ci'][0])}–{pct(e['fp_rate_ci'][1])}] "
                 f"| {pct(e['fn_rate'])} [{pct(e['fn_rate_ci'][0])}–{pct(e['fn_rate_ci'][1])}] | {e['auc']:.3f} [{e['auc_ci'][0]:.3f}–{e['auc_ci'][1]:.3f}] | {e['threshold']:.4g} |")
    L += [""]
    for name, mc in s["mcnemar"].items():
        L.append(f"McNemar exact test, calibrated VRT vs `{mc['against']}` ({name.replace('_', ' ')}): only VRT correct on {mc['only_a_correct']} pairs, only the other on {mc['only_b_correct']}, p = {mc['p_value']:.2g}.")
        L.append("")
    cv = s["calibration"]["cross_validation_5fold"]
    L += [f"Calibration split: FP {pct(s['calibration']['calibration']['fp_rate'])}, FN {pct(s['calibration']['calibration']['fn_rate'])}; "
          f"5-fold CV inside it (selection optimism check): accuracy {pct(cv['accuracy_mean'])} ± {pct(cv['accuracy_sd'])}.", "",
          "## Image-space noise on the test scenes (holdout)", "",
          "Four families (JPEG, Gaussian, resample, gamma) have counterparts on the calibration scenes (*seen*); WebP and 0.5 px blur occur only here (*unseen*).", "",
          "| noise | seen in calibration | FP / n | FP rate [95 % CI] | regressions detected (recall) |", "|---|---|---|---|---|"]
    for k, h in s["holdout"].items():
        if k == "all":
            continue
        L.append(f"| {k} | {'yes' if h['seen_in_calibration'] else 'no'} | {h['false_positives']}/{h['n_neg']} | {pct(h['fp_rate'])} [{pct(h['fp_ci'][0])}–{pct(h['fp_ci'][1])}] | {h['detected']}/{h['n_pos']} ({pct(h['recall'])}) |")
    h = s["holdout"]["all"]
    L += ["", f"Overall holdout: FP {pct(h['fp_rate'])} [{pct(h['fp_ci'][0])}–{pct(h['fp_ci'][1])}], FN {pct(h['fn_rate'])} [{pct(h['fn_ci'][0])}–{pct(h['fn_ci'][1])}].", "",
          "## Stress tier (unchanged by intent, layout drifted ≥ 1 px)", "",
          f"{s['stress']['false_positives']}/{s['stress']['n']} flagged ({pct(s['stress']['fp_rate'])} [{pct(s['stress']['ci'][0])}–{pct(s['stress']['ci'][1])}]). "
          "Font-metric drift above one pixel contradicts the 2 px floor of real shift regressions; it is fixed by pinning the rendering environment, not by tolerances.", "",
          "## Errors by category (calibration + test, core and stress)", "", "| class | category | tier | n | errors | rate [95 % CI] |", "|---|---|---|---|---|---|"]
    for r in s["per_category"]:
        L.append(f"| {'changed' if r['label'] else 'unchanged'} | {r['category']} | {r['tier']} | {r['n']} | {r['errors']} | {pct(r['error_rate'])} [{pct(r['ci'][0])}–{pct(r['ci'][1])}] |")
    L += ["", "## Detection limits (clean mutations of known size)", ""]
    for kind, d in s["sensitivity_curves"].items():
        L.append(f"**{kind}** ({d['unit']}): " + ", ".join(f"{p['magnitude']:g}→{pct(p['rate'])}" for p in d["points"]))
        L.append("")
    L += ["## Threshold sensitivity (share of test verdicts that flip)", "", "| parameter change | flips | FP rate | FN rate |", "|---|---|---|---|"]
    for k, v in s["threshold_sensitivity"].items():
        L.append(f"| {k} | {pct(v['flip_rate'])} | {pct(v['fp_rate'])} | {pct(v['fn_rate'])} |")
    lat = s["latency_ms"]
    L += ["", f"Comparison latency incl. SSIM/PSNR/pHash extras on 1280×720: p50 {lat['p50']:.0f} ms, p95 {lat['p95']:.0f} ms."]
    (RESULTS / "RESULTS.md").write_text("\n".join(L) + "\n")
