import numpy as np
import pytest

from diff_engine import ConfigError, DiffConfig, compare_images
from diff_engine.engine import significant_pixels

from conftest import make_ui


def cfg(**kw):
    base = {"color": {"delta_e": 2.3}, "geometry": {"radius_px": 1}, "antialiasing": {"blur_sigma_px": 1.0},
            "regions": {"min_area_px": 8, "merge_px": 3}, "decision": {"fail_pixels": 50}}
    for k, v in kw.items():
        base[k] = {**base.get(k, {}), **v} if isinstance(v, dict) else v
    return DiffConfig.from_dict(base)


def test_identical_images_pass(ui):
    r = compare_images(ui, ui.copy(), cfg())
    assert r.passed and r.diff_pixels == 0 and r.raw_diff_pixels == 0


def test_solid_change_fails_and_is_localised(ui):
    b = ui.copy()
    b[40:100, 16:150] = (200, 30, 30)
    r = compare_images(ui, b, cfg())
    assert not r.passed
    assert r.diff_pixels > 3000
    reg = r.regions[0]
    assert 10 <= reg["x"] <= 20 and 35 <= reg["y"] <= 45


def test_subthreshold_colour_change_is_tolerated(ui):
    b = ui.copy()
    b[40:100, 16:150] = (240, 244, 254)  # 1 level in blue: far below dE 2.3
    r = compare_images(ui, b, cfg())
    assert r.raw_diff_pixels > 0 and r.diff_pixels == 0 and r.passed


def test_ignore_region_hides_change(ui):
    b = ui.copy()
    b[40:100, 170:300] = (0, 0, 0)
    assert not compare_images(ui, b, cfg()).passed
    r = compare_images(ui, b, cfg(), ignore_regions=[{"x": 170, "y": 40, "w": 130, "h": 60}])
    assert r.passed and r.ignored_pixels > 0


def test_subpixel_shift_noise_is_tolerated_but_real_shift_is_not():
    a = make_ui(seed=4)
    from scipy import ndimage as ndi

    soft = np.clip(np.stack([ndi.shift(a[..., c].astype(float), (0.3, 0.3), order=1, mode="nearest") for c in range(3)], -1), 0, 255).astype(np.uint8)
    assert compare_images(a, soft, cfg(color={"delta_e": 2.3}, geometry={"radius_px": 1})).passed
    moved = np.roll(a, 6, axis=1)
    assert not compare_images(a, moved, cfg()).passed


def test_size_mismatch_fails_and_pad_policy_can_override(ui):
    taller = np.vstack([ui, np.full((20, ui.shape[1], 3), 255, np.uint8)])
    r = compare_images(ui, taller, cfg())
    assert not r.passed and r.size_mismatch
    assert any("size_mismatch" in x for x in r.reasons)


def test_alpha_difference_is_reported():
    a = np.full((40, 40, 4), 255, np.uint8)
    b = a.copy()
    b[10:30, 10:30, 3] = 120
    r = compare_images(a, b, cfg(decision={"fail_pixels": 10}))
    assert not r.passed
    ok = compare_images(a, b, cfg(alpha={"tolerance": 200}, decision={"fail_pixels": 10}))
    assert ok.passed


def test_tiny_difference_below_min_area_is_dropped(ui):
    b = ui.copy()
    b[5, 5] = (0, 0, 0)
    assert compare_images(ui, b, cfg(regions={"min_area_px": 8}, decision={"fail_pixels": 1})).passed


def test_keep_suspects_matches_region_filter(ui):
    b = ui.copy()
    b[40:100, 16:150] = (200, 30, 30)
    c = cfg()
    r = compare_images(ui, b, c, keep_suspects=True)
    assert significant_pixels(r.suspects, 8, 3) == r.diff_pixels


def test_config_validation_and_merge():
    with pytest.raises(ConfigError):
        DiffConfig.from_dict({"colour": {}})
    with pytest.raises(ConfigError):
        DiffConfig.from_dict({"color": {"delta_e": -1}})
    m = DiffConfig().merged({"color": {"delta_e": 5}})
    assert m.color.delta_e == 5 and m.geometry.radius_px == DiffConfig().geometry.radius_px


def test_accepts_png_bytes(ui):
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.fromarray(ui).save(buf, "PNG")
    assert compare_images(buf.getvalue(), ui, cfg()).passed
