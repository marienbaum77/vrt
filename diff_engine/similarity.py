"""Global similarity metrics used as comparison baselines: SSIM, PSNR, perceptual hash.

SSIM follows Wang et al. (2004) with an 11x11 Gaussian window (sigma=1.5); it is
validated against scikit-image in ``tests/test_similarity.py``.
"""
from __future__ import annotations

from typing import Optional, Tuple

import numpy as np
from scipy import fft as sfft
from scipy.ndimage import gaussian_filter


def to_gray(rgb: np.ndarray) -> np.ndarray:
    """ITU-R BT.601 luma as float64 in 0..255."""
    f = rgb.astype(np.float64)
    return 0.299 * f[..., 0] + 0.587 * f[..., 1] + 0.114 * f[..., 2]


def ssim_map(x: np.ndarray, y: np.ndarray, data_range: float = 255.0, sigma: float = 1.5) -> np.ndarray:
    """Return the full SSIM map (same shape as the 2-D inputs, border not cropped)."""
    x = x.astype(np.float64)
    y = y.astype(np.float64)
    kw = dict(sigma=sigma, truncate=3.5, mode="reflect")
    ux = gaussian_filter(x, **kw)
    uy = gaussian_filter(y, **kw)
    uxx = gaussian_filter(x * x, **kw)
    uyy = gaussian_filter(y * y, **kw)
    uxy = gaussian_filter(x * y, **kw)
    vx = uxx - ux * ux
    vy = uyy - uy * uy
    vxy = uxy - ux * uy
    c1 = (0.01 * data_range) ** 2
    c2 = (0.03 * data_range) ** 2
    num = (2 * ux * uy + c1) * (2 * vxy + c2)
    den = (ux * ux + uy * uy + c1) * (vx + vy + c2)
    return num / den


def ssim(
    a_rgb: np.ndarray, b_rgb: np.ndarray, valid: Optional[np.ndarray] = None, sigma: float = 1.5
) -> Tuple[float, float]:
    """Mean SSIM and the low-percentile (0.1 %) SSIM over valid pixels (luma channel).

    The second value is far more sensitive to small local changes than the mean,
    which is diluted by the large unchanged area of a UI screenshot.
    """
    m = ssim_map(to_gray(a_rgb), to_gray(b_rgb), sigma=sigma)
    pad = 5
    core = m[pad:-pad, pad:-pad] if min(m.shape) > 2 * pad else m
    if valid is not None:
        vcore = valid[pad:-pad, pad:-pad] if min(m.shape) > 2 * pad else valid
        vals = core[vcore]
        if vals.size == 0:
            return 1.0, 1.0
    else:
        vals = core.ravel()
    return float(vals.mean()), float(np.percentile(vals, 0.1))


def psnr(a_rgb: np.ndarray, b_rgb: np.ndarray, valid: Optional[np.ndarray] = None, cap_db: float = 100.0) -> float:
    d = a_rgb.astype(np.float64) - b_rgb.astype(np.float64)
    se = (d * d).mean(axis=-1)
    mse = float(se[valid].mean()) if valid is not None and valid.any() else float(se.mean())
    if mse <= 1e-12:
        return cap_db
    return float(min(cap_db, 10.0 * np.log10(255.0**2 / mse)))


def phash(rgb: np.ndarray, hash_size: int = 8, highfreq_factor: int = 4) -> int:
    """64-bit DCT perceptual hash (Zauner 2010 / pHash style)."""
    from PIL import Image

    size = hash_size * highfreq_factor
    img = Image.fromarray(rgb.astype(np.uint8)).convert("L").resize((size, size), Image.LANCZOS)
    pix = np.asarray(img, dtype=np.float64)
    dct = sfft.dctn(pix, norm="ortho")[:hash_size, :hash_size]
    med = np.median(dct.ravel()[1:])  # skip the DC term
    bits = (dct.ravel() > med).astype(np.uint8)
    out = 0
    for b in bits:
        out = (out << 1) | int(b)
    return out


def hamming(h1: int, h2: int) -> int:
    return bin(h1 ^ h2).count("1")
