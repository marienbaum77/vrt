"""Comparison engine: tolerant pixel-diff pipeline.

Stages (each one only ever *removes* pixels from the suspect set, so the cost is
proportional to the amount of difference, not to the image size):

    0. align sizes, build ignore mask, composite alpha, equalise ignored zones
    1. exact diff            -> raw_diff_pixels
    2. colour tolerance      -> colour_diff_pixels   (CIEDE2000 > delta_e)
    3. geometry tolerance    -> geometry_diff_pixels (no match within radius in the *other* image)
    4. anti-aliasing blur    -> blur_diff_pixels     (still different after Gaussian low-pass)
    5. region filter         -> diff_pixels          (clusters >= min_area_px)
"""
from __future__ import annotations

import io
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

import numpy as np
from PIL import Image
from scipy import ndimage as ndi

from .color import delta_e_2000, delta_e_76, rgb_max_abs, srgb_float_to_lab, srgb_to_lab
from .config import DiffConfig
from .similarity import hamming, phash, psnr, ssim

ImageLike = Union[str, Path, bytes, Image.Image, np.ndarray]
Region = Dict[str, Any]


@dataclass
class DiffResult:
    passed: bool
    width: int
    height: int
    compared_pixels: int
    ignored_pixels: int
    size_mismatch: bool
    baseline_size: Tuple[int, int]
    candidate_size: Tuple[int, int]
    raw_diff_pixels: int = 0
    color_diff_pixels: int = 0
    geometry_diff_pixels: int = 0
    blur_diff_pixels: int = 0
    diff_pixels: int = 0
    diff_ratio: float = 0.0
    max_delta_e: float = 0.0
    mean_delta_e: float = 0.0
    regions: List[Region] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)
    ssim: Optional[float] = None
    ssim_p01: Optional[float] = None
    psnr: Optional[float] = None
    phash_distance: Optional[int] = None
    timings_ms: Dict[str, float] = field(default_factory=dict)
    diff_time_ms: float = 0.0
    fast_failed: bool = False
    # artefacts for reports (not serialised)
    artifacts: Dict[str, np.ndarray] = field(default_factory=dict, repr=False)
    # pixels that survived colour/geometry/blur stages, before the region filter (used by calibration sweeps)
    suspects: Optional[Dict[str, Any]] = field(default=None, repr=False)

    def to_dict(self) -> Dict[str, Any]:
        d = {k: v for k, v in self.__dict__.items() if k not in ("artifacts", "suspects")}
        d["baseline_size"] = list(self.baseline_size)
        d["candidate_size"] = list(self.candidate_size)
        return d

    def scores(self) -> Dict[str, float]:
        """Scalar 'how different' scores of every comparison algorithm (higher = more different)."""
        s: Dict[str, float] = {
            "pixel_exact": float(self.raw_diff_pixels),
            "pixel_color": float(self.color_diff_pixels),
            "pixel_geometry": float(self.geometry_diff_pixels),
            "pixel_blur": float(self.blur_diff_pixels),
            "pixel_tolerant": float(self.diff_pixels),
        }
        if self.ssim is not None:
            s["ssim_mean"] = 1.0 - self.ssim
        if self.ssim_p01 is not None:
            s["ssim_low"] = 1.0 - self.ssim_p01
        if self.psnr is not None:
            s["psnr"] = -self.psnr
        if self.phash_distance is not None:
            s["phash"] = float(self.phash_distance)
        return s


# --------------------------------------------------------------------------- IO
def load_image(src: ImageLike) -> np.ndarray:
    """Load an image as an RGBA uint8 array (H, W, 4)."""
    if isinstance(src, np.ndarray):
        arr = src
        if arr.ndim == 2:
            arr = np.stack([arr] * 3, axis=-1)
        if arr.shape[2] == 3:
            arr = np.concatenate([arr, np.full(arr.shape[:2] + (1,), 255, dtype=arr.dtype)], axis=2)
        return arr.astype(np.uint8, copy=False)
    if isinstance(src, (bytes, bytearray)):
        src = io.BytesIO(bytes(src))
    img = src if isinstance(src, Image.Image) else Image.open(src)
    return np.asarray(img.convert("RGBA"), dtype=np.uint8)


def _composite_on_white(rgba: np.ndarray) -> np.ndarray:
    a = rgba[..., 3:4].astype(np.uint16)
    if (a == 255).all():
        return np.ascontiguousarray(rgba[..., :3])
    rgb = (rgba[..., :3].astype(np.uint16) * a + 255 * (255 - a)) // 255
    return rgb.astype(np.uint8)


def build_ignore_mask(h: int, w: int, regions: Optional[Iterable[Region]], padding: int = 0) -> np.ndarray:
    mask = np.zeros((h, w), dtype=bool)
    for r in regions or ():
        x, y, rw, rh = int(round(r["x"])), int(round(r["y"])), int(np.ceil(r["w"])), int(np.ceil(r["h"]))
        x0, y0 = max(0, x - padding), max(0, y - padding)
        x1, y1 = min(w, x + rw + padding), min(h, y + rh + padding)
        if x1 > x0 and y1 > y0:
            mask[y0:y1, x0:x1] = True
    return mask


# ------------------------------------------------------------------ distances
class _Metric:
    """Bundles a feature transform and a distance function for the chosen colour metric."""

    def __init__(self, name: str):
        self.name = name

    def feat(self, rgb_u8: np.ndarray) -> np.ndarray:
        return rgb_u8 if self.name == "rgb" else srgb_to_lab(rgb_u8)

    def feat_float(self, rgb_f: np.ndarray) -> np.ndarray:
        return rgb_f if self.name == "rgb" else srgb_float_to_lab(rgb_f)

    def dist(self, f1: np.ndarray, f2: np.ndarray) -> np.ndarray:
        if self.name == "ciede2000":
            return delta_e_2000(f1, f2)
        if self.name == "cie76":
            return delta_e_76(f1, f2)
        return rgb_max_abs(f1, f2)


def _offsets(radius: int) -> List[Tuple[int, int]]:
    return [(dy, dx) for dy in range(-radius, radius + 1) for dx in range(-radius, radius + 1) if (dy, dx) != (0, 0)]


# ----------------------------------------------------------------------- core
def compare_images(
    baseline: ImageLike,
    candidate: ImageLike,
    cfg: Optional[DiffConfig] = None,
    ignore_regions: Optional[Sequence[Region]] = None,
    keep_artifacts: bool = False,
    keep_suspects: bool = False,
) -> DiffResult:
    cfg = (cfg or DiffConfig()).validate()
    t_all = time.perf_counter()
    tm: Dict[str, float] = {}

    def lap(name: str, t0: float) -> float:
        now = time.perf_counter()
        tm[name] = round((now - t0) * 1000.0, 3)
        return now

    t = time.perf_counter()
    A4 = load_image(baseline)
    B4 = load_image(candidate)
    t = lap("load", t)

    base_size = (A4.shape[1], A4.shape[0])
    cand_size = (B4.shape[1], B4.shape[0])
    size_mismatch = base_size != cand_size
    reasons: List[str] = []
    if size_mismatch:
        reasons.append(f"size_mismatch: baseline {base_size[0]}x{base_size[1]} vs candidate {cand_size[0]}x{cand_size[1]}")
        H, W = max(A4.shape[0], B4.shape[0]), max(A4.shape[1], B4.shape[1])
        A4 = _pad_to(A4, H, W, (255, 0, 255, 255))
        B4 = _pad_to(B4, H, W, (0, 255, 255, 255))
    H, W = A4.shape[:2]

    ignore = build_ignore_mask(H, W, ignore_regions, cfg.mask_padding_px)
    alpha_diff = np.abs(A4[..., 3].astype(np.int16) - B4[..., 3].astype(np.int16)) > cfg.alpha.tolerance
    A = _composite_on_white(A4)
    B = _composite_on_white(B4)
    if ignore.any():
        B = B.copy()
        B[ignore] = A[ignore]
        alpha_diff &= ~ignore
    t = lap("prepare", t)

    metric = _Metric(cfg.color.metric)
    neq = np.any(A != B, axis=2)
    raw_diff = int(neq.sum())
    idx = np.flatnonzero(neq)
    t = lap("exact_diff", t)

    compared = int(H * W - ignore.sum())
    ys_all, xs_all = np.divmod(idx, W)

    # --- stage 2: colour tolerance -------------------------------------------------
    forced = np.zeros(0, dtype=bool)
    if idx.size:
        fa = metric.feat(A[ys_all, xs_all])
        fb = metric.feat(B[ys_all, xs_all])
        d0 = metric.dist(fa, fb)
        keep = d0 > cfg.color.delta_e
        idx, ys, xs, d0 = idx[keep], ys_all[keep], xs_all[keep], d0[keep]
    else:
        ys, xs, d0 = ys_all, xs_all, np.zeros(0, dtype=np.float64)
    forced = np.zeros(idx.size, dtype=bool)
    # alpha-channel differences always count as significant
    if alpha_diff.any():
        ay, ax = np.nonzero(alpha_diff)
        aidx = ay * W + ax
        new = ~np.isin(aidx, idx)
        idx = np.concatenate([idx, aidx[new]])
        ys = np.concatenate([ys, ay[new]])
        xs = np.concatenate([xs, ax[new]])
        d0 = np.concatenate([d0, np.full(int(new.sum()), 100.0)])
        forced = np.concatenate([forced, np.ones(int(new.sum()), dtype=bool)])
    color_diff = int(idx.size)
    t = lap("color", t)

    # --- fast path: the page is obviously different ----------------------------------
    fast_failed = bool(compared and color_diff / compared >= cfg.fast_fail_ratio)

    # --- stage 3: geometry tolerance -------------------------------------------------
    r = cfg.geometry.radius_px
    if not fast_failed and r > 0 and idx.size:
        explained = _explained_by_neighbours(A, B, ys, xs, r, metric, cfg.geometry_delta_e, cfg.geometry.blend)
        keep = ~explained | forced  # alpha-only differences cannot be explained geometrically
        idx, ys, xs, d0, forced = idx[keep], ys[keep], xs[keep], d0[keep], forced[keep]
    geometry_diff = int(idx.size)
    t = lap("geometry", t)

    # --- stage 4: anti-aliasing blur tolerance ---------------------------------------
    sigma = cfg.antialiasing.blur_sigma_px
    if not fast_failed and sigma > 0 and idx.size:
        keep = _survives_blur(A, B, ys, xs, sigma, metric, cfg.blur_delta_e) | forced
        idx, ys, xs, d0, forced = idx[keep], ys[keep], xs[keep], d0[keep], forced[keep]
    blur_diff = int(idx.size)
    t = lap("antialiasing", t)

    # --- stage 5: region grouping / min area -----------------------------------------
    sig = np.zeros((H, W), dtype=bool)
    sig[ys, xs] = True
    regions, final_mask = _regions(sig, ys, xs, d0, cfg.regions.min_area_px, cfg.regions.merge_px)
    diff_pixels = int(final_mask.sum())
    t = lap("regions", t)

    res = DiffResult(
        passed=True,
        width=W,
        height=H,
        compared_pixels=compared,
        ignored_pixels=int(ignore.sum()),
        size_mismatch=size_mismatch,
        baseline_size=base_size,
        candidate_size=cand_size,
        raw_diff_pixels=raw_diff,
        color_diff_pixels=color_diff,
        geometry_diff_pixels=geometry_diff,
        blur_diff_pixels=blur_diff,
        diff_pixels=diff_pixels,
        diff_ratio=(diff_pixels / compared) if compared else 0.0,
        max_delta_e=float(d0.max()) if d0.size else 0.0,
        mean_delta_e=float(d0.mean()) if d0.size else 0.0,
        regions=regions,
        reasons=reasons,
        fast_failed=fast_failed,
    )

    # --- extras (comparison baselines) -------------------------------------------------
    if cfg.extras:
        valid = ~ignore
        if "ssim" in cfg.extras:
            res.ssim, res.ssim_p01 = ssim(A, B, valid)
        if "psnr" in cfg.extras:
            res.psnr = psnr(A, B, valid)
        if "phash" in cfg.extras:
            res.phash_distance = hamming(phash(A), phash(B))
        t = lap("extras", t)

    # --- decision ----------------------------------------------------------------------
    dec = cfg.decision
    if diff_pixels >= dec.fail_pixels:
        reasons.append(f"{diff_pixels} significant pixels >= fail_pixels={dec.fail_pixels}")
    if dec.fail_ratio is not None and res.diff_ratio >= dec.fail_ratio:
        reasons.append(f"diff_ratio {res.diff_ratio:.6f} >= fail_ratio={dec.fail_ratio}")
    if dec.ssim_min is not None and res.ssim is not None and res.ssim < dec.ssim_min:
        reasons.append(f"ssim {res.ssim:.5f} < ssim_min={dec.ssim_min}")
    if size_mismatch and cfg.size_policy == "fail":
        pass  # reason already recorded
    elif size_mismatch:
        reasons = [x for x in reasons if not x.startswith("size_mismatch")]
    res.reasons = reasons
    res.passed = not reasons
    res.timings_ms = tm
    res.diff_time_ms = round((time.perf_counter() - t_all) * 1000.0, 3)

    if keep_suspects:
        res.suspects = {"shape": (H, W), "ys": ys, "xs": xs, "d0": d0, "compared": compared}
    if keep_artifacts:
        tolerated = neq & ~final_mask & ~ignore
        res.artifacts = {
            "baseline": A,
            "candidate": B,
            "diff_mask": final_mask,
            "tolerated_mask": tolerated,
            "ignore_mask": ignore,
        }
    return res


def significant_pixels(suspects: Dict[str, Any], min_area_px: int, merge_px: int) -> int:
    """Re-run only the region filter on cached suspect pixels (cheap re-evaluation of region thresholds)."""
    ys, xs = suspects["ys"], suspects["xs"]
    if ys.size == 0:
        return 0
    H, W = suspects["shape"]
    sig = np.zeros((H, W), dtype=bool)
    sig[ys, xs] = True
    _, final = _regions(sig, ys, xs, suspects["d0"], min_area_px, merge_px)
    return int(final.sum())


def _pad_to(arr: np.ndarray, h: int, w: int, fill: Tuple[int, int, int, int]) -> np.ndarray:
    if arr.shape[0] == h and arr.shape[1] == w:
        return arr
    out = np.empty((h, w, 4), dtype=np.uint8)
    out[:] = np.array(fill, dtype=np.uint8)
    out[: arr.shape[0], : arr.shape[1]] = arr
    return out


def _explained_by_neighbours(
    A: np.ndarray,
    B: np.ndarray,
    ys: np.ndarray,
    xs: np.ndarray,
    radius: int,
    metric: _Metric,
    tol: float,
    blend: bool = True,
) -> np.ndarray:
    """Geometry / anti-aliasing tolerance.

    A suspect pixel p is *explained* (forgiven) when, in both directions,
      B[p] lies within ``tol`` of a colour blend t*A[q] + (1-t)*A[p] for some q within
      ``radius`` of p (and the same with A and B swapped).
    With ``blend=False`` only exact neighbour colours (t = 1) are allowed.
    Pixels sitting in a flat neighbourhood in either image can never be explained
    (nothing nearby to blend with), which makes large solid-colour changes cheap.
    """
    H, W = A.shape[:2]
    n = ys.size
    out = np.zeros(n, dtype=bool)
    if n == 0:
        return out
    offs = _offsets(radius)
    Ap, Bp = A[ys, xs], B[ys, xs]
    NA, NB = [], []
    flat_a = np.ones(n, dtype=bool)
    flat_b = np.ones(n, dtype=bool)
    for dy, dx in offs:
        qy = np.clip(ys + dy, 0, H - 1)
        qx = np.clip(xs + dx, 0, W - 1)
        na, nb = A[qy, qx], B[qy, qx]
        NA.append(na)
        NB.append(nb)
        flat_a &= (na == Ap).all(axis=1)
        flat_b &= (nb == Bp).all(axis=1)
    act = np.flatnonzero(~(flat_a | flat_b))
    if act.size == 0:
        return out
    ap, bp = Ap[act], Bp[act]
    feat_a, feat_b = metric.feat(ap), metric.feat(bp)
    best_ab = np.full(act.size, np.inf)  # B[p] vs blends of A[p]..A[q]
    best_ba = np.full(act.size, np.inf)  # A[p] vs blends of B[p]..B[q]
    for k in range(len(offs)):
        np.minimum(best_ab, _segment_distance(ap, NA[k][act], feat_b, metric, blend, bp), out=best_ab)
        np.minimum(best_ba, _segment_distance(bp, NB[k][act], feat_a, metric, blend, ap), out=best_ba)
    out[act] = (best_ab <= tol) & (best_ba <= tol)
    return out


def _segment_distance(
    xp: np.ndarray, xq: np.ndarray, feat_y: np.ndarray, metric: _Metric, blend: bool, yp: np.ndarray
) -> np.ndarray:
    """Distance from colour y to the closest point of the segment [xp, xq] (RGB blend)."""
    if not blend:
        return metric.dist(metric.feat(xq), feat_y)
    xpf = xp.astype(np.float32)
    seg = xq.astype(np.float32) - xpf
    den = (seg * seg).sum(axis=1)
    num = ((yp.astype(np.float32) - xpf) * seg).sum(axis=1)
    t = np.clip(np.where(den > 0, num / np.where(den > 0, den, 1.0), 0.0), 0.0, 1.0)
    proj = xpf + t[:, None] * seg
    return metric.dist(metric.feat_float(proj), feat_y)


def _blur_delta(A: np.ndarray, B: np.ndarray, ys: np.ndarray, xs: np.ndarray, sigma: float, metric: _Metric) -> np.ndarray:
    """Colour distance of the suspect pixels after both images are low-passed with a Gaussian."""
    H, W = A.shape[:2]
    pad = int(np.ceil(4 * sigma)) + 1
    y0, y1 = max(0, int(ys.min()) - pad), min(H, int(ys.max()) + pad + 1)
    x0, x1 = max(0, int(xs.min()) - pad), min(W, int(xs.max()) + pad + 1)
    sa = ndi.gaussian_filter(A[y0:y1, x0:x1].astype(np.float32), sigma=(sigma, sigma, 0), mode="nearest")
    sb = ndi.gaussian_filter(B[y0:y1, x0:x1].astype(np.float32), sigma=(sigma, sigma, 0), mode="nearest")
    ly, lx = ys - y0, xs - x0
    return metric.dist(metric.feat_float(sa[ly, lx]), metric.feat_float(sb[ly, lx]))


def _survives_blur(
    A: np.ndarray, B: np.ndarray, ys: np.ndarray, xs: np.ndarray, sigma: float, metric: _Metric, tol: float
) -> np.ndarray:
    """True for suspect pixels that still differ after both images are low-passed."""
    return _blur_delta(A, B, ys, xs, sigma, metric) > tol


def _regions(
    sig: np.ndarray, ys: np.ndarray, xs: np.ndarray, de: np.ndarray, min_area: int, merge_px: int
) -> Tuple[List[Region], np.ndarray]:
    H, W = sig.shape
    if not sig.any():
        return [], np.zeros_like(sig)
    grown = ndi.binary_dilation(sig, structure=np.ones((3, 3), bool), iterations=merge_px) if merge_px > 0 else sig
    labels, n = ndi.label(grown, structure=np.ones((3, 3), dtype=int))
    lab_at = labels[ys, xs]
    areas = np.bincount(lab_at, minlength=n + 1)
    keep_label = areas >= min_area
    keep_label[0] = False
    keep_px = keep_label[lab_at]
    final = np.zeros((H, W), dtype=bool)
    final[ys[keep_px], xs[keep_px]] = True
    regions: List[Region] = []
    for lab in np.flatnonzero(keep_label):
        sel = lab_at == lab
        yy, xx = ys[sel], xs[sel]
        regions.append(
            {
                "x": int(xx.min()),
                "y": int(yy.min()),
                "w": int(xx.max() - xx.min() + 1),
                "h": int(yy.max() - yy.min() + 1),
                "area": int(sel.sum()),
                "max_delta_e": round(float(de[sel].max()), 3),
            }
        )
    regions.sort(key=lambda r: r["area"], reverse=True)
    return regions, final
