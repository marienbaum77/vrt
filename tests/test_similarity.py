import numpy as np
from skimage.metrics import peak_signal_noise_ratio, structural_similarity

from diff_engine.similarity import hamming, phash, psnr, ssim, to_gray

from conftest import make_ui


def test_ssim_matches_skimage():
    a = make_ui(seed=1)
    b = a.copy()
    b[50:90, 30:80] = (200, 30, 30)
    ga, gb = to_gray(a), to_gray(b)
    ref = structural_similarity(ga, gb, data_range=255, gaussian_weights=True, sigma=1.5, use_sample_covariance=False, win_size=11)
    mean, p01 = ssim(a, b)
    assert abs(mean - ref) < 2e-3
    assert p01 <= mean


def test_ssim_identical_is_one():
    a = make_ui()
    assert ssim(a, a)[0] == 1.0


def test_psnr_matches_skimage():
    a = make_ui(seed=3)
    rng = np.random.default_rng(0)
    b = np.clip(a.astype(int) + rng.integers(-6, 7, a.shape), 0, 255).astype(np.uint8)
    assert abs(psnr(a, b) - peak_signal_noise_ratio(a, b, data_range=255)) < 1e-6
    assert psnr(a, a) == 100.0


def test_phash_distance():
    a = make_ui(seed=1)
    assert hamming(phash(a), phash(a)) == 0
    c = a.copy()
    c[:, :] = 255 - c
    assert hamming(phash(a), phash(c)) > 8
