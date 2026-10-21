"""Two additional, separately-reported sets built on top of the main dataset.

``holdout``      image-space noise (JPEG, Gaussian sensor noise, sub-pixel bilinear resampling, gamma drift,
                 and two families that exist *only* here: WebP recompression and a 0.5 px blur) applied to the
                 screenshots of the *test* scenes.  Negatives: baseline vs perturbed baseline; positives:
                 baseline vs perturbed *mutated* screenshot.  The four "seen" families are also generated for the
                 *calibration* scenes (``calibration_noise``) so the threshold is calibrated against them too;
                 WebP and blur are never seen during calibration.
``sensitivity``  one mutation family swept over its magnitude (shift px, colour dE, resize px) on fresh scenes.
                 Answers: "what is the smallest regression the chosen thresholds can still see?"

    python -m test_dataset.extras holdout     --data test_dataset/data
    python -m test_dataset.extras sensitivity --data test_dataset/data
"""
from __future__ import annotations

import argparse
import io
import json
import random
import sys
import time
from multiprocessing import get_context
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from PIL import Image
from scipy import ndimage as ndi

from capture import CaptureSession, CaptureSpec
from capture.stabilize import Stabilize

from .generate import _rgb, _save_png, _spec, visible_pixels
from .mutations import PROBE_JS, NoTarget, plan_mutation
from .scenes import build_scene

SENSITIVITY_GRID: Dict[str, Tuple[str, List[float]]] = {
    "shift": ("px", [1, 2, 3, 4, 6, 8]),
    "resize": ("px", [1, 2, 3, 5, 8, 12]),
    "color_bg": ("dE2000", [1, 1.5, 2, 3, 5, 8, 12, 20]),
    "color_text": ("dE2000", [3, 5, 8, 12, 20, 40]),
}


# ------------------------------------------------------------------------------------ holdout
def perturb(rgb: np.ndarray, kind: str, rng: random.Random) -> Tuple[np.ndarray, str]:
    f = rgb.astype(np.float32)
    if kind == "jpeg":
        q = rng.choice([80, 85, 90, 95])
        buf = io.BytesIO()
        Image.fromarray(rgb).save(buf, "JPEG", quality=q)
        return np.asarray(Image.open(buf).convert("RGB")), f"jpeg q={q}"
    if kind == "gauss":
        sigma = rng.uniform(1.0, 2.5)
        nz = np.random.default_rng(rng.randrange(2**31)).normal(0, sigma, f.shape)
        return np.clip(f + nz, 0, 255).round().astype(np.uint8), f"gauss sigma={sigma:.2f}"
    if kind == "resample":
        dx, dy = rng.uniform(0.2, 0.5) * rng.choice([-1, 1]), rng.uniform(0.2, 0.5) * rng.choice([-1, 1])
        out = np.stack([ndi.shift(f[..., c], (dy, dx), order=1, mode="nearest") for c in range(3)], axis=-1)
        return np.clip(out, 0, 255).round().astype(np.uint8), f"bilinear shift ({dx:.2f},{dy:.2f}) px"
    if kind == "webp":
        q = rng.choice([85, 90, 95])
        buf = io.BytesIO()
        Image.fromarray(rgb).save(buf, "WEBP", quality=q)
        return np.asarray(Image.open(buf).convert("RGB")), f"webp q={q}"
    if kind == "blur":
        sg = rng.uniform(0.4, 0.6)
        out = np.stack([ndi.gaussian_filter(f[..., c], sg) for c in range(3)], axis=-1)
        return np.clip(out, 0, 255).round().astype(np.uint8), f"gaussian blur sigma={sg:.2f}"
    if kind == "gamma":
        g = rng.uniform(0.985, 1.015)
        return np.clip(255.0 * (f / 255.0) ** g, 0, 255).round().astype(np.uint8), f"gamma {g:.3f}"
    raise ValueError(kind)


SEEN_KINDS = ("jpeg", "gauss", "resample", "gamma")  # also used to augment the calibration set (calibration scenes)
UNSEEN_KINDS = ("webp", "blur")  # appear only in the holdout


def build_noise_set(data: Path, name: str, split: str, kinds, n_per_kind: int, seed: int) -> Dict[str, Any]:
    """Image-space noise pairs built on the screenshots of the scenes of one split (calibration or test)."""
    man = json.loads((data / "manifest.json").read_text())
    rng = random.Random(seed)
    scene_pairs = [p for p in man["pairs"] if p["split"] == split]
    pos = [p for p in scene_pairs if p["label"] == 1 and p["tier"] == "core"]
    out_dir = data / name
    out_dir.mkdir(exist_ok=True)
    pairs: List[Dict[str, Any]] = []
    for kind in kinds:
        bases = rng.sample(sorted({p["baseline"] for p in scene_pairs}), n_per_kind)
        for i, b in enumerate(bases):
            for label in (0, 1):
                if label == 1:
                    cand = rng.choice([p for p in pos if p["baseline"] == b] or pos)
                    b = cand["baseline"]
                    src_rgb = np.asarray(Image.open(data / cand["candidate"]).convert("RGB"))
                    regions, category = cand["regions"], f"{cand['category']}+{kind}"
                else:
                    src_rgb = np.asarray(Image.open(data / b).convert("RGB"))
                    regions, category = [], kind
                noisy, detail = perturb(src_rgb, kind, rng)
                pid = f"{name[:3]}_{kind}_{i:02d}{'p' if label else 'n'}"
                Image.fromarray(noisy).save(out_dir / f"{pid}.png", optimize=True)
                pairs.append({"id": pid, "label": label, "category": category, "noise_kind": kind, "detail": detail, "split": "holdout" if name == "holdout" else split,
                              "tier": "core", "baseline": b, "candidate": f"{name}/{pid}.png", "regions": regions, "scene": -1})
    meta = {"name": f"vrt-{name}", "scenes_from": split, "kinds": list(kinds), "seed": seed,
            "counts": {"pairs": len(pairs), "changed": sum(p["label"] for p in pairs)}, "pairs": pairs}
    (data / f"{name}_manifest.json").write_text(json.dumps(meta, indent=1))
    return meta


def build_holdout(data: Path, n_per_kind: int = 15) -> Dict[str, Any]:
    cal = build_noise_set(data, "calibration_noise", "calibration", SEEN_KINDS, n_per_kind, seed=11)
    hold = build_noise_set(data, "holdout", "test", SEEN_KINDS + UNSEEN_KINDS, n_per_kind, seed=7)
    return {"calibration_noise": cal["counts"], "holdout": hold["counts"]}


# -------------------------------------------------------------------------------- sensitivity
def _sens_worker(args: Tuple[int, List[int], Path, int]) -> List[Dict[str, Any]]:
    wid, scene_ids, out_dir, seed = args
    recs: List[Dict[str, Any]] = []
    with CaptureSession() as sess:
        for sid in scene_ids:
            scene = build_scene(sid, seed)
            base = sess.capture(_spec(f"s{sid:03d}"), html=scene.html)
            base_rgb = _rgb(base.png)
            _save_png(base.png, out_dir / "baselines" / f"s{sid:03d}.png")
            probe = lambda tok, u, lo, hi: sess.probe(scene.html, PROBE_JS, [tok, u, lo, hi])  # noqa: E731
            for kind, (unit, mags) in SENSITIVITY_GRID.items():
                for mag in mags:
                    rng = random.Random(f"{seed}-{sid}-{kind}-{mag}")
                    for _ in range(8):
                        try:
                            plan = plan_mutation(kind, probe, rng, force=mag)
                            cand = sess.capture(_spec("c", js=plan.js), html=scene.html)
                        except Exception:  # noqa: BLE001 - no suitable element, try another
                            continue
                        pid = f"x{sid:03d}_{kind}_{str(mag).replace('.', '_')}"
                        _save_png(cand.png, out_dir / "candidates" / f"{pid}.png")
                        recs.append({"id": pid, "scene": sid, "kind": kind, "unit": unit, "magnitude": float(mag), "realised": plan.magnitude,
                                     "detail": plan.detail, "visible_px": visible_pixels(base_rgb, _rgb(cand.png)),
                                     "baseline": f"sensitivity/baselines/s{sid:03d}.png", "candidate": f"sensitivity/candidates/{pid}.png",
                                     "regions": [{k: r[k] for k in ("x", "y", "w", "h")} for r in base.regions + cand.regions]})
                        break
            print(f"[sens w{wid}] scene {sid}", flush=True)
    return recs


def build_sensitivity(data: Path, scenes: int = 12, first_scene: int = 1000, seed: int = 2026, workers: int = 2) -> Dict[str, Any]:
    out_dir = data / "sensitivity"
    out_dir.mkdir(exist_ok=True)
    ids = list(range(first_scene, first_scene + scenes))
    jobs = [(i, ids[i::workers], out_dir, seed) for i in range(workers) if ids[i::workers]]
    t0 = time.time()
    with get_context("spawn").Pool(len(jobs)) as pool:
        res = pool.map(_sens_worker, jobs)
    pairs = sorted((r for rs in res for r in rs), key=lambda r: r["id"])
    meta = {"name": "vrt-sensitivity", "seed": seed, "grid": {k: v[1] for k, v in SENSITIVITY_GRID.items()}, "build_seconds": round(time.time() - t0, 1), "pairs": pairs}
    (data / "sensitivity_manifest.json").write_text(json.dumps(meta, indent=1))
    return meta


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Build holdout-noise and sensitivity sets")
    ap.add_argument("what", choices=["holdout", "sensitivity", "all"])
    ap.add_argument("--data", default="test_dataset/data")
    ap.add_argument("--workers", type=int, default=2)
    a = ap.parse_args(argv)
    data = Path(a.data)
    if a.what in ("holdout", "all"):
        m = build_holdout(data)
        print("noise sets:", m)
    if a.what in ("sensitivity", "all"):
        m = build_sensitivity(data, workers=a.workers)
        print("sensitivity:", len(m["pairs"]), "pairs in", m["build_seconds"], "s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
