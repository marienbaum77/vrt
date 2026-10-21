"""Shared helpers of the verification experiments."""
from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass
from multiprocessing import Pool
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

import numpy as np

from diff_engine import DiffConfig, compare_images

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "test_dataset" / "data"
RESULTS = ROOT / "results"

# Scalar "how different" scores produced by one engine run (higher = more different).
# The first five form the cumulative tolerance ladder: each stage adds one tolerance mechanism.
LADDER = ["pixel_exact", "pixel_color", "pixel_geometry", "pixel_blur", "pixel_tolerant"]
GLOBAL_BASELINES = ["psnr", "ssim_mean", "ssim_low", "phash"]
LABELS = {
    "pixel_exact": "S0 exact pixel diff",
    "pixel_color": "S1 + colour tolerance (dE2000)",
    "pixel_geometry": "S2 + geometry tolerance (r px, blend)",
    "pixel_blur": "S3 + anti-aliasing low-pass",
    "pixel_tolerant": "S4 + region filter (VRT)",
    "psnr": "PSNR",
    "ssim_mean": "SSIM (mean)",
    "ssim_low": "SSIM (0.1 % percentile)",
    "phash": "pHash (Hamming)",
}


def load_manifest(name: str = "manifest.json", data: Path = DATA) -> Dict[str, Any]:
    return json.loads((data / name).read_text())


def calibrated_config(cal: Dict[str, Any]) -> DiffConfig:
    """Engine config of the calibrated operating point (see ``results/calibration.json``)."""
    c = cal["config"]
    return DiffConfig.from_dict(
        {
            "color": {"delta_e": 2.3},
            "geometry": {"radius_px": c["radius_px"], "blend": True},
            "antialiasing": {"blur_sigma_px": c["blur_sigma_px"], "delta_e": c["blur_delta_e"]},
            "regions": {"min_area_px": c["min_area_px"], "merge_px": c["merge_px"]},
            "decision": {"fail_pixels": max(1, math.ceil(cal["fail_pixels"]))},  # score >= thr  <=>  count >= ceil(thr)
            "extras": ["ssim", "psnr", "phash"],
        }
    )


_G: Dict[str, Any] = {}


def _init(data: str, cfg: Dict[str, Any]) -> None:
    _G["data"], _G["cfg"] = Path(data), DiffConfig.from_dict(cfg)


def _score_one(p: Dict[str, Any]) -> Dict[str, Any]:
    r = compare_images(_G["data"] / p["baseline"], _G["data"] / p["candidate"], _G["cfg"], p.get("regions") or None)
    row = {"id": p["id"], "label": p["label"], "category": p["category"], "split": p["split"], "tier": p.get("tier", "core"),
           "time_ms": r.diff_time_ms, "diff_pixels": r.diff_pixels, "max_delta_e": round(r.max_delta_e, 3)}
    row.update(r.scores())
    return row


def score_pairs(pairs: Sequence[Dict[str, Any]], cfg: DiffConfig, data: Path = DATA, workers: int = 2) -> List[Dict[str, Any]]:
    """Run the engine on every pair and return one row of scores per pair."""
    with Pool(workers, initializer=_init, initargs=(str(data), cfg.to_dict())) as pool:
        return pool.map(_score_one, list(pairs), chunksize=4)


def write_csv(rows: Sequence[Dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cols = list(rows[0])
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


def read_csv(path: Path) -> List[Dict[str, Any]]:
    out = []
    with open(path) as fh:
        for r in csv.DictReader(fh):
            row: Dict[str, Any] = {}
            for k, v in r.items():
                if k in ("id", "category", "split", "tier"):
                    row[k] = v
                elif k == "label":
                    row[k] = int(v)
                else:
                    row[k] = float(v) if v not in ("", None) else float("nan")
            out.append(row)
    return out


def arr(rows: Sequence[Dict[str, Any]], key: str) -> np.ndarray:
    return np.array([r[key] for r in rows], dtype=float)


def pct(x: float) -> str:
    return f"{100 * x:.1f}%"
