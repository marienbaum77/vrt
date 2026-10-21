import numpy as np
from skimage import color

from diff_engine.color import delta_e_2000, srgb_to_lab


def test_ciede2000_sharma_reference_pairs():
    # Sharma, Wu, Dalal (2005) test data, pairs 1-3
    ref = [((50.0, 2.6772, -79.7751), (50.0, 0.0, -82.7485), 2.0425),
           ((50.0, 3.1571, -77.2803), (50.0, 0.0, -82.7485), 2.8615),
           ((50.0, 2.8361, -74.0200), (50.0, 0.0, -82.7485), 3.4412)]
    for a, b, expected in ref:
        assert abs(float(delta_e_2000(np.array(a), np.array(b))) - expected) < 1e-3


def test_ciede2000_matches_skimage_on_random_lab():
    rng = np.random.default_rng(1)
    a = np.c_[rng.uniform(0, 100, 2000), rng.uniform(-100, 100, 2000), rng.uniform(-100, 100, 2000)]
    b = np.c_[rng.uniform(0, 100, 2000), rng.uniform(-100, 100, 2000), rng.uniform(-100, 100, 2000)]
    assert np.abs(delta_e_2000(a, b) - color.deltaE_ciede2000(a, b)).max() < 1e-6


def test_srgb_to_lab_matches_skimage():
    rng = np.random.default_rng(2)
    rgb = rng.integers(0, 256, (500, 3), dtype=np.uint8)
    ref = color.rgb2lab(rgb[None].astype(np.float64) / 255.0)[0]
    assert np.abs(srgb_to_lab(rgb) - ref).max() < 0.02


def test_identical_colours_have_zero_distance():
    lab = srgb_to_lab(np.array([[10, 200, 30]], dtype=np.uint8))
    assert float(delta_e_2000(lab, lab)[0]) == 0.0
