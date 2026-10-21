"""Colour science helpers: sRGB -> CIELAB and colour-difference metrics.

All functions are vectorised with NumPy.  ``delta_e_2000`` follows Sharma, Wu &
Dalal (2005); it is cross-checked against scikit-image in ``tests/test_color.py``.
"""
from __future__ import annotations

import numpy as np

# sRGB (D65) -> XYZ
_M = np.array(
    [
        [0.4124564, 0.3575761, 0.1804375],
        [0.2126729, 0.7151522, 0.0721750],
        [0.0193339, 0.1191920, 0.9503041],
    ],
    dtype=np.float64,
)
_WHITE = np.array([0.95047, 1.0, 1.08883], dtype=np.float64)


def _build_lut() -> np.ndarray:
    c = np.arange(256, dtype=np.float64) / 255.0
    return np.where(c > 0.04045, ((c + 0.055) / 1.055) ** 2.4, c / 12.92).astype(np.float32)


_SRGB_TO_LINEAR = _build_lut()
_EPS = 216.0 / 24389.0  # 0.008856
_KAPPA_F = 841.0 / 108.0  # 7.787


def srgb_to_lab(rgb_u8: np.ndarray) -> np.ndarray:
    """Convert uint8 sRGB array of shape (..., 3) to float32 CIELAB (D65)."""
    lin = _SRGB_TO_LINEAR[rgb_u8].astype(np.float64)
    xyz = lin @ _M.T / _WHITE
    f = np.where(xyz > _EPS, np.cbrt(xyz), _KAPPA_F * xyz + 16.0 / 116.0)
    fx, fy, fz = f[..., 0], f[..., 1], f[..., 2]
    lab = np.stack([116.0 * fy - 16.0, 500.0 * (fx - fy), 200.0 * (fy - fz)], axis=-1)
    return lab.astype(np.float32)


def srgb_float_to_lab(rgb_f: np.ndarray) -> np.ndarray:
    """Same as :func:`srgb_to_lab` for float RGB in [0, 255] (e.g. after blurring)."""
    c = np.clip(rgb_f.astype(np.float64), 0.0, 255.0) / 255.0
    lin = np.where(c > 0.04045, ((c + 0.055) / 1.055) ** 2.4, c / 12.92)
    xyz = lin @ _M.T / _WHITE
    f = np.where(xyz > _EPS, np.cbrt(xyz), _KAPPA_F * xyz + 16.0 / 116.0)
    fx, fy, fz = f[..., 0], f[..., 1], f[..., 2]
    lab = np.stack([116.0 * fy - 16.0, 500.0 * (fx - fy), 200.0 * (fy - fz)], axis=-1)
    return lab.astype(np.float32)


def delta_e_76(lab1: np.ndarray, lab2: np.ndarray) -> np.ndarray:
    d = lab1.astype(np.float64) - lab2.astype(np.float64)
    return np.sqrt((d * d).sum(axis=-1))


def delta_e_2000(lab1: np.ndarray, lab2: np.ndarray, kL: float = 1.0, kC: float = 1.0, kH: float = 1.0) -> np.ndarray:
    """CIEDE2000 colour difference between two Lab arrays of shape (..., 3)."""
    lab1 = lab1.astype(np.float64)
    lab2 = lab2.astype(np.float64)
    L1, a1, b1 = lab1[..., 0], lab1[..., 1], lab1[..., 2]
    L2, a2, b2 = lab2[..., 0], lab2[..., 1], lab2[..., 2]

    C1 = np.hypot(a1, b1)
    C2 = np.hypot(a2, b2)
    Cbar = (C1 + C2) / 2.0
    Cbar7 = Cbar**7
    G = 0.5 * (1.0 - np.sqrt(Cbar7 / (Cbar7 + 25.0**7)))
    a1p = (1.0 + G) * a1
    a2p = (1.0 + G) * a2
    C1p = np.hypot(a1p, b1)
    C2p = np.hypot(a2p, b2)
    h1p = np.degrees(np.arctan2(b1, a1p)) % 360.0
    h2p = np.degrees(np.arctan2(b2, a2p)) % 360.0
    zero_c = (C1p * C2p) == 0.0

    dLp = L2 - L1
    dCp = C2p - C1p
    dh = h2p - h1p
    dh = np.where(dh > 180.0, dh - 360.0, dh)
    dh = np.where(dh < -180.0, dh + 360.0, dh)
    dh = np.where(zero_c, 0.0, dh)
    dHp = 2.0 * np.sqrt(C1p * C2p) * np.sin(np.radians(dh) / 2.0)

    Lbp = (L1 + L2) / 2.0
    Cbp = (C1p + C2p) / 2.0
    hsum = h1p + h2p
    hbp = np.where(
        np.abs(h1p - h2p) <= 180.0,
        hsum / 2.0,
        np.where(hsum < 360.0, (hsum + 360.0) / 2.0, (hsum - 360.0) / 2.0),
    )
    hbp = np.where(zero_c, hsum, hbp)

    T = (
        1.0
        - 0.17 * np.cos(np.radians(hbp - 30.0))
        + 0.24 * np.cos(np.radians(2.0 * hbp))
        + 0.32 * np.cos(np.radians(3.0 * hbp + 6.0))
        - 0.20 * np.cos(np.radians(4.0 * hbp - 63.0))
    )
    d_theta = 30.0 * np.exp(-(((hbp - 275.0) / 25.0) ** 2))
    Cbp7 = Cbp**7
    Rc = 2.0 * np.sqrt(Cbp7 / (Cbp7 + 25.0**7))
    Sl = 1.0 + (0.015 * (Lbp - 50.0) ** 2) / np.sqrt(20.0 + (Lbp - 50.0) ** 2)
    Sc = 1.0 + 0.045 * Cbp
    Sh = 1.0 + 0.015 * Cbp * T
    Rt = -np.sin(np.radians(2.0 * d_theta)) * Rc

    tl = dLp / (kL * Sl)
    tc = dCp / (kC * Sc)
    th = dHp / (kH * Sh)
    return np.sqrt(np.maximum(tl * tl + tc * tc + th * th + Rt * tc * th, 0.0))


def rgb_max_abs(rgb1: np.ndarray, rgb2: np.ndarray) -> np.ndarray:
    """Chebyshev distance in raw RGB (0..255) — the 'naive' per-channel tolerance."""
    return np.abs(rgb1.astype(np.float32) - rgb2.astype(np.float32)).max(axis=-1)
