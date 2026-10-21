"""Automatic discovery of volatile (dynamic) regions from repeated captures of the same page.

The page is loaded N times with real time (no frozen clock); every pixel that differs between any
two captures belongs to dynamic content (clocks, counters, rotating banners, ads).  The union is
dilated and turned into bounding boxes that the comparison engine treats like explicit ignore zones.
"""
from __future__ import annotations

import io
from typing import Dict, List, Sequence

import numpy as np
from PIL import Image
from scipy import ndimage as ndi


def _rgb(png: bytes) -> np.ndarray:
    with Image.open(io.BytesIO(png)) as im:
        return np.asarray(im.convert("RGB"), dtype=np.int16)


def discover_volatile_regions(
    frames: Sequence[bytes], tolerance: int = 8, dilate_px: int = 4, min_area_px: int = 24
) -> List[Dict[str, float]]:
    """Return ``[{"selector": "auto", x, y, w, h}, ...]`` for pixels that are unstable across ``frames``."""
    if len(frames) < 2:
        return []
    arrays = [_rgb(f) for f in frames]
    h = min(a.shape[0] for a in arrays)
    w = min(a.shape[1] for a in arrays)
    ref = arrays[0][:h, :w]
    volatile = np.zeros((h, w), dtype=bool)
    for a in arrays[1:]:
        volatile |= np.abs(a[:h, :w] - ref).max(axis=2) > tolerance
    if not volatile.any():
        return []
    grown = ndi.binary_dilation(volatile, structure=np.ones((3, 3), bool), iterations=max(1, dilate_px))
    labels, n = ndi.label(grown)
    out: List[Dict[str, float]] = []
    for sl in ndi.find_objects(labels):
        ys, xs = sl
        box_h, box_w = ys.stop - ys.start, xs.stop - xs.start
        if volatile[sl].sum() < min_area_px:
            continue
        out.append({"selector": "auto", "index": len(out), "x": float(xs.start), "y": float(ys.start), "w": float(box_w), "h": float(box_h)})
    return out
