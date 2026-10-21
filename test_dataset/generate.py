"""Generate the labelled visual-regression dataset (baseline/candidate pairs + ground truth).

    python -m test_dataset.generate --out test_dataset/data --scenes 80 --seed 2026

Every pair is rendered by the *same* capture module the product uses.  Ground truth is
known by construction: positives carry exactly one real mutation, negatives carry only
rendering noise / dynamic content that is covered by ``[data-vrt-ignore]`` masks.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import multiprocessing as mp
import random
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from PIL import Image

from capture import CaptureSession, CaptureSpec
from capture.stabilize import Stabilize

from .mutations import (
    FLOORS,
    NEGATIVE_WEIGHTS,
    OVERLAY_NOISE,
    POSITIVE_WEIGHTS,
    PROBE_JS,
    NoTarget,
    merge_noise,
    plan_mutation,
    plan_noise,
)
from .scenes import IGNORE_SELECTOR, build_scene

PAIRS_PER_CLASS_PER_SCENE = 3
# Labelling criteria (engine-independent, fixed a priori - see docs/DATASET.md):
VISIBLE_PX = 30  # a mutation is "visible" if >= 30 px of the clean render differ by dE2000 >= 5
VISIBLE_DE = 5.0
MAX_NOISE_DISPLACEMENT_PX = 1.0  # "unchanged" pairs must not move any element / text node by >= 1 px (DOM-measured)
NOSUB_ARGS = ["--disable-font-subpixel-positioning"]


def _balanced(weights: Dict[str, int], n: int, rng: random.Random) -> List[str]:
    total = sum(weights.values())
    raw = {k: n * v / total for k, v in weights.items()}
    counts = {k: int(v) for k, v in raw.items()}
    for k in sorted(raw, key=lambda k: raw[k] - counts[k], reverse=True)[: n - sum(counts.values())]:
        counts[k] += 1
    out = [k for k, c in counts.items() for _ in range(c)]
    rng.shuffle(out)
    return out


def _spec(name: str, extra_css: str = "", js: str = "") -> CaptureSpec:
    return CaptureSpec(
        name=name,
        mask_selectors=[IGNORE_SELECTOR],
        extra_css=extra_css,
        pre_capture_js=js,
        stabilize=Stabilize(max_attempts=3, settle_ms=60),
        collect_geometry=True,
    )


def _rgb(png: bytes) -> np.ndarray:
    return np.asarray(Image.open(io.BytesIO(png)).convert("RGB"))


def _save_png(png: bytes, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.open(io.BytesIO(png)).convert("RGB").save(path, optimize=True)


def visible_pixels(a: np.ndarray, b: np.ndarray, min_de: float = VISIBLE_DE) -> int:
    """Number of pixels whose CIEDE2000 difference is >= ``min_de`` (plain per-pixel metric, no tolerances)."""
    from diff_engine.color import delta_e_2000, srgb_to_lab

    neq = np.any(a != b, axis=2)
    if not neq.any():
        return 0
    return int((delta_e_2000(srgb_to_lab(a[neq]), srgb_to_lab(b[neq])) >= min_de).sum())


def max_displacement(ga: List[List[float]], gb: List[List[float]]) -> float:
    """Largest movement (px) of any element / text-node edge between two renders; inf if the DOM boxes differ in number."""
    if len(ga) != len(gb):
        return float("inf")
    if not ga:
        return 0.0
    return float(np.abs(np.asarray(ga) - np.asarray(gb)).max())


def _union(*region_lists: List[dict]) -> List[dict]:
    out = []
    for lst in region_lists:
        for r in lst:
            out.append({k: r[k] for k in ("x", "y", "w", "h")})
    return out


def _worker(args: Tuple[int, List[int], Path, int, List[str], List[str]]) -> List[Dict[str, Any]]:
    wid, scene_ids, out_dir, seed, pos_cats, neg_cats = args
    records: List[Dict[str, Any]] = []
    with CaptureSession() as default, CaptureSession(launch_args=NOSUB_ARGS) as nosub:
        sessions = {"default": default, "nosub": nosub}
        for sid in scene_ids:
            t0 = time.time()
            scene = build_scene(sid, seed)
            base = default.capture(_spec(f"s{sid:03d}"), html=scene.html)
            base_rgb = _rgb(base.png)
            _save_png(base.png, out_dir / "baselines" / f"s{sid:03d}.png")
            has_dynamic = bool(base.meta["regions"])
            probe = lambda tok, u, lo, hi: default.probe(scene.html, PROBE_JS, [tok, u, lo, hi])  # noqa: E731

            for slot in range(PAIRS_PER_CLASS_PER_SCENE):
                g = sid * PAIRS_PER_CLASS_PER_SCENE + slot
                # ---------------- positive (changed) ----------------
                rng = random.Random(f"{seed}-{sid}-{slot}-pos")
                requested = pos_cats[g]
                # if the scene has no suitable target for the requested mutation, fall back to another
                # category so the classes stay balanced (the fallback is recorded in the manifest)
                order = [requested] + rng.sample([k for k in POSITIVE_WEIGHTS if k != requested], len(POSITIVE_WEIGHTS) - 1)
                rec = None
                for kind in order[:4]:
                    for attempt in range(8):
                        try:
                            plan = plan_mutation(kind, probe, rng)
                            clean = default.capture(_spec("c", js=plan.js), html=scene.html)
                        except Exception:
                            continue
                        n_diff = visible_pixels(base_rgb, _rgb(clean.png))
                        if n_diff < VISIBLE_PX:
                            continue
                        noise_names: List[str] = []
                        final = clean
                        if rng.random() < 0.5:
                            pool = [n for n in OVERLAY_NOISE if has_dynamic or n != "masked_dynamic"]
                            nz = plan_noise(rng.choice(pool), rng, has_dynamic)
                            try:
                                final = sessions[nz.variant].capture(_spec("c", extra_css=nz.css, js=plan.js + nz.js), html=scene.html)
                                noise_names = nz.names
                            except Exception:
                                final, noise_names = clean, []
                        pid = f"p{sid:03d}{slot}p"
                        _save_png(final.png, out_dir / "candidates" / f"{pid}.png")
                        rec = {
                            "id": pid, "scene": sid, "label": 1, "category": kind, "magnitude": plan.magnitude,
                            "unit": plan.unit, "detail": plan.detail, "noise": noise_names, "visible_px_clean": n_diff,
                            "max_displacement_px": round(max_displacement(base.meta["geometry"], final.meta["geometry"]), 3), "tier": "core",
                            "baseline": f"baselines/s{sid:03d}.png", "candidate": f"candidates/{pid}.png",
                            "regions": _union(base.regions, final.regions),
                        }
                        if kind != requested:
                            rec["fallback_from"] = requested
                        break
                    if rec is not None:
                        break
                if rec is None:
                    print(f"[w{wid}] scene {sid} slot {slot}: no mutation applicable", file=sys.stderr)
                else:
                    records.append(rec)

                # ---------------- negative (unchanged) ----------------
                rng = random.Random(f"{seed}-{sid}-{slot}-neg")
                kind = neg_cats[g]
                nz = None
                for _ in range(6):
                    try:
                        nz = plan_noise(kind, rng, has_dynamic)
                        break
                    except NoTarget:
                        kind = rng.choice([k for k in NEGATIVE_WEIGHTS if k != "masked_dynamic"])
                if nz is None:
                    continue
                cand = sessions[nz.variant].capture(_spec("c", extra_css=nz.css, js=nz.js), html=scene.html)
                pid = f"p{sid:03d}{slot}n"
                _save_png(cand.png, out_dir / "candidates" / f"{pid}.png")
                disp = max_displacement(base.meta["geometry"], cand.meta["geometry"])
                records.append(
                    {
                        "id": pid, "scene": sid, "label": 0, "category": nz.names[0].split(":")[0] if nz.names[0].startswith("combined") else nz.names[0],
                        "magnitude": 0, "unit": "", "detail": ",".join(nz.names), "noise": nz.names,
                        "visible_px_clean": visible_pixels(base_rgb, _rgb(cand.png)),
                        "max_displacement_px": round(disp, 3),
                        # noise that moves layout by >= 1 px is a *stress* case: it contradicts the 2 px floor of
                        # real "shift" regressions, so it is reported separately and excluded from the headline metric
                        "tier": "core" if disp < MAX_NOISE_DISPLACEMENT_PX else "stress",
                        "baseline": f"baselines/s{sid:03d}.png", "candidate": f"candidates/{pid}.png",
                        "regions": _union(base.regions, cand.regions),
                    }
                )
            print(f"[w{wid}] scene {sid:03d} {scene.layout:7s} {','.join(scene.components):28s} {time.time() - t0:5.1f}s", flush=True)
    return records


def build_dataset(out_dir: Path, n_scenes: int = 80, seed: int = 2026, workers: int = 2) -> Dict[str, Any]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    plan_rng = random.Random(seed)
    n = n_scenes * PAIRS_PER_CLASS_PER_SCENE
    pos_cats = _balanced(POSITIVE_WEIGHTS, n, plan_rng)
    neg_cats = _balanced(NEGATIVE_WEIGHTS, n, plan_rng)
    ids = list(range(n_scenes))
    chunks = [ids[i::workers] for i in range(workers)]
    t0 = time.time()
    jobs = [(i, c, out_dir, seed, pos_cats, neg_cats) for i, c in enumerate(chunks) if c]
    if len(jobs) == 1:
        results = [_worker(jobs[0])]
    else:
        with mp.get_context("spawn").Pool(len(jobs)) as pool:
            results = pool.map(_worker, jobs)
    pairs = sorted((r for rs in results for r in rs), key=lambda r: r["id"])
    for p in pairs:  # split by scene -> no scene leaks between calibration and test
        p["split"] = "calibration" if p["scene"] % 2 == 0 else "test"
    h = hashlib.sha256()
    for p in pairs:
        for key in ("baseline", "candidate"):
            h.update((out_dir / p[key]).read_bytes())
    manifest = {
        "name": "vrt-synthetic-ui",
        "version": 1,
        "seed": seed,
        "scenes": n_scenes,
        "viewport": [1280, 720],
        "ignore_selector": IGNORE_SELECTOR,
        "counts": {
            "pairs": len(pairs),
            "changed": sum(p["label"] for p in pairs),
            "unchanged": sum(1 - p["label"] for p in pairs),
            "core": sum(p["tier"] == "core" for p in pairs),
            "stress": sum(p["tier"] == "stress" for p in pairs),
            "calibration": sum(p["split"] == "calibration" for p in pairs),
            "test": sum(p["split"] == "test" for p in pairs),
        },
        "positive_weights": POSITIVE_WEIGHTS,
        "negative_weights": NEGATIVE_WEIGHTS,
        "visibility_floors": FLOORS,
        "labelling": {"visible_px": VISIBLE_PX, "visible_delta_e": VISIBLE_DE, "max_noise_displacement_px": MAX_NOISE_DISPLACEMENT_PX},
        "content_sha256": h.hexdigest(),
        "build_seconds": round(time.time() - t0, 1),
        "pairs": pairs,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=1))
    return manifest


def load_manifest(data_dir: Path) -> Dict[str, Any]:
    return json.loads((Path(data_dir) / "manifest.json").read_text())


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Build the labelled VRT dataset")
    ap.add_argument("--out", default="test_dataset/data")
    ap.add_argument("--scenes", type=int, default=100)
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--workers", type=int, default=2)
    a = ap.parse_args(argv)
    m = build_dataset(Path(a.out), a.scenes, a.seed, a.workers)
    print(json.dumps(m["counts"], indent=2), "sha256:", m["content_sha256"][:16], f"({m['build_seconds']}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
