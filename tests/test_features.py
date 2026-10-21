"""The cached grid evaluation must equal the real engine for the same parameters."""
from diff_engine import DiffConfig, compare_images
from experiments.features import extract, significant_pixels

from conftest import make_ui


def test_cached_path_equals_engine():
    a = make_ui(seed=2)
    b = a.copy()
    b[40:100, 16:150] = (200, 30, 30)
    b[120:126, 20:60] = (220, 220, 220)
    for r, s, tol, merge, area in [(1, 1.0, 2.3, 3, 8), (2, 1.5, 3.5, 3, 8), (0, 0.0, 2.3, 1, 1), (2, 2.0, 5.0, 1, 24)]:
        cfg = DiffConfig.from_dict({"geometry": {"radius_px": r}, "antialiasing": {"blur_sigma_px": s, "delta_e": tol},
                                    "regions": {"min_area_px": area, "merge_px": merge}})
        eng = compare_images(a, b, cfg).diff_pixels
        sus, _, _ = extract(a, b, None, radii=[r], sigmas=[s])
        ys, xs, d = sus[(r, s)]
        assert significant_pixels(ys, xs, d, tol, merge, area) == eng, (r, s, tol, merge, area)
