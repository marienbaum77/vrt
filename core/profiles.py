"""Named threshold profiles.

``balanced`` is the profile calibrated on the labelled dataset (see ``experiments/`` and
``results/calibration.json``; keep the two in sync after re-calibrating); ``strict`` and ``lenient`` bracket it.  A project selects one with
``diff.profile`` and may override any single value (``diff.color.delta_e: 3``) or, per scenario,
``scenarios[].diff``.
"""
from __future__ import annotations

from typing import Dict

from diff_engine.config import ConfigError, DiffConfig

_PROFILES: Dict[str, dict] = {
    # pixel-perfect: any perceptible colour change anywhere fails (expect flaky builds across machines)
    "strict": {
        "color": {"metric": "ciede2000", "delta_e": 1.0},
        "geometry": {"radius_px": 0},
        "antialiasing": {"blur_sigma_px": 0.0},
        "regions": {"min_area_px": 1, "merge_px": 0},
        "decision": {"fail_pixels": 1},
    },
    # calibrated on the calibration split of the reference dataset (results/calibration.json, minimax FP/FN);
    # fail_pixels = ceil(9.39)
    "balanced": {
        "color": {"metric": "ciede2000", "delta_e": 2.3},
        "geometry": {"radius_px": 1, "blend": True},
        "antialiasing": {"blur_sigma_px": 2.0, "delta_e": 5.0},
        "regions": {"min_area_px": 8, "merge_px": 3},
        "decision": {"fail_pixels": 10},
    },
    # tolerant of font hinting / cross-OS drift; will miss small regressions
    "lenient": {
        "color": {"metric": "ciede2000", "delta_e": 4.0},
        "geometry": {"radius_px": 2, "blend": True},
        "antialiasing": {"blur_sigma_px": 2.5, "delta_e": 8.0},
        "regions": {"min_area_px": 16, "merge_px": 4},
        "decision": {"fail_pixels": 60},
    },
}


def profile_names() -> list:
    return sorted(_PROFILES)


def get_profile(name: str) -> DiffConfig:
    try:
        return DiffConfig.from_dict(_PROFILES[name])
    except KeyError:
        raise ConfigError(f"unknown profile {name!r}; available: {profile_names()}") from None


def profile_dict(name: str) -> dict:
    get_profile(name)
    return _PROFILES[name]
