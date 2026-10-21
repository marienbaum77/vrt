"""Cheap re-evaluation of the diff pipeline for many parameter combinations.

The expensive stages (colour tolerance, geometry tolerance, Gaussian low-pass) depend on few
parameters; their per-pixel outputs are cached as *suspect pixels with a residual dE*.  The cheap
final stages (dE threshold -> region grouping -> pixel count) are then swept over a grid.  A unit test
asserts that this path gives exactly the same pixel counts as ``diff_engine.compare_images``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
from scipy import ndimage as ndi

from diff_engine.color import srgb_to_lab
from diff_engine.engine import (
    _Metric,
    _blur_delta,
    _composite_on_white,
    _explained_by_neighbours,
    _pad_to,
    build_ignore_mask,
    load_image,
)

Suspects = Dict[Tuple[int, float], Tuple[np.ndarray, np.ndarray, np.ndarray]]


def extract(
    baseline: Any,
    candidate: Any,
    ignore_regions: Optional[Sequence[dict]],
    radii: Iterable[int] = (1,),
    sigmas: Iterable[float] = (1.0,),
    color_delta_e: float = 2.3,
    mask_padding: int = 2,
    blend: bool = True,
    min_residual: float = 0.5,
) -> Tuple[Suspects, Tuple[int, int], int]:
    """Return ``{(radius, sigma): (ys, xs, dE_after_blur)}``, image shape and compared-pixel count.

    sigma == 0 means "no blur": the stored dE is the colour dE of the pixel (after the geometry stage).
    """
    A4, B4 = load_image(baseline), load_image(candidate)
    if A4.shape != B4.shape:
        H, W = max(A4.shape[0], B4.shape[0]), max(A4.shape[1], B4.shape[1])
        A4, B4 = _pad_to(A4, H, W, (255, 0, 255, 255)), _pad_to(B4, H, W, (0, 255, 255, 255))
    H, W = A4.shape[:2]
    ignore = build_ignore_mask(H, W, ignore_regions, mask_padding)
    A, B = _composite_on_white(A4), _composite_on_white(B4)
    if ignore.any():
        B = B.copy()
        B[ignore] = A[ignore]
    metric = _Metric("ciede2000")
    neq = np.any(A != B, axis=2)
    idx = np.flatnonzero(neq)
    out: Suspects = {}
    compared = int(H * W - ignore.sum())
    if idx.size == 0:
        for r in radii:
            for s in sigmas:
                out[(r, s)] = (np.zeros(0, np.int32), np.zeros(0, np.int32), np.zeros(0, np.float32))
        return out, (H, W), compared
    ys, xs = np.divmod(idx, W)
    d0 = metric.dist(metric.feat(A[ys, xs]), metric.feat(B[ys, xs]))
    keep = d0 > color_delta_e
    ys, xs = ys[keep], xs[keep]
    for r in radii:
        if r > 0 and ys.size:
            ex = _explained_by_neighbours(A, B, ys, xs, r, metric, color_delta_e, blend)
            gy, gx = ys[~ex], xs[~ex]
        else:
            gy, gx = ys, xs
        for s in sigmas:
            if gy.size == 0:
                out[(r, s)] = (np.zeros(0, np.int32), np.zeros(0, np.int32), np.zeros(0, np.float32))
                continue
            if s > 0:
                d = _blur_delta(A, B, gy, gx, s, metric)
            else:
                d = metric.dist(metric.feat(A[gy, gx]), metric.feat(B[gy, gx]))
            k = d > min_residual
            out[(r, s)] = (gy[k].astype(np.int32), gx[k].astype(np.int32), d[k].astype(np.float32))
    return out, (H, W), compared


def significant_pixels(ys: np.ndarray, xs: np.ndarray, d: np.ndarray, tol: float, merge: int, min_area: int) -> int:
    """Pixels in connected groups (grouped after dilating by ``merge``) of >= ``min_area`` pixels with dE > ``tol``."""
    k = d > tol
    if not k.any():
        return 0
    ys, xs = ys[k], xs[k]
    y0, x0 = int(ys.min()) - merge - 1, int(xs.min()) - merge - 1
    h, w = int(ys.max()) - y0 + merge + 2, int(xs.max()) - x0 + merge + 2
    sig = np.zeros((h, w), dtype=bool)
    sig[ys - y0, xs - x0] = True
    grown = ndi.binary_dilation(sig, structure=np.ones((3, 3), bool), iterations=merge) if merge > 0 else sig
    labels, n = ndi.label(grown, structure=np.ones((3, 3), dtype=int))
    areas = np.bincount(labels[ys - y0, xs - x0], minlength=n + 1)
    areas[0] = 0
    return int(areas[areas >= min_area].sum())
