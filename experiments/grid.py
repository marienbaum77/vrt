"""Grid of tolerance parameters evaluated on a dataset split -> matrix of 'significant pixel' scores."""
from __future__ import annotations

import itertools
import json
import time
from multiprocessing import Pool
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

import numpy as np

from .features import extract, significant_pixels

RADII = (0, 1, 2, 3)
SIGMAS = (0.0, 1.0, 1.5, 2.0, 3.0)
TOLS = (2.3, 3.5, 5.0, 8.0, 12.0)
MERGES = (1, 3)
MIN_AREAS = (1, 8, 24)

CONFIGS: List[Dict[str, Any]] = [
    {"radius_px": r, "blur_sigma_px": s, "blur_delta_e": t, "merge_px": m, "min_area_px": a}
    for r, s, t, m, a in itertools.product(RADII, SIGMAS, TOLS, MERGES, MIN_AREAS)
]

_DATA: Path


def _work(p: Dict[str, Any]) -> Tuple[str, np.ndarray]:
    sus, _, _ = extract(_DATA / p["baseline"], _DATA / p["candidate"], p["regions"], RADII, SIGMAS)
    row = np.zeros(len(CONFIGS), dtype=np.float32)
    cache: Dict[Tuple, int] = {}
    for i, c in enumerate(CONFIGS):
        ys, xs, d = sus[(c["radius_px"], c["blur_sigma_px"])]
        key = (c["radius_px"], c["blur_sigma_px"], c["blur_delta_e"], c["merge_px"], c["min_area_px"])
        row[i] = significant_pixels(ys, xs, d, c["blur_delta_e"], c["merge_px"], c["min_area_px"])
    return p["id"], row


def _init(data_dir: str) -> None:
    global _DATA
    _DATA = Path(data_dir)


def run_grid(data_dir: Path, pairs: Sequence[Dict[str, Any]], workers: int = 2) -> Tuple[List[str], np.ndarray]:
    t0 = time.time()
    with Pool(workers, initializer=_init, initargs=(str(data_dir),)) as pool:
        out = pool.map(_work, list(pairs), chunksize=2)
    ids = [o[0] for o in out]
    mat = np.stack([o[1] for o in out])
    print(f"grid: {len(pairs)} pairs x {len(CONFIGS)} configs in {time.time() - t0:.0f}s")
    return ids, mat
