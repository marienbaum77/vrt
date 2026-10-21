"""Typed, validated configuration of the comparison engine (thresholds / tolerances)."""
from __future__ import annotations

from dataclasses import MISSING, asdict, dataclass, field, fields, is_dataclass
from typing import Any, Dict, List, Optional


class ConfigError(ValueError):
    """Raised for invalid or unknown configuration keys."""


@dataclass
class ColorTolerance:
    """Per-pixel colour tolerance.

    metric: ``ciede2000`` (perceptual, default) | ``cie76`` | ``rgb`` (max abs channel diff).
    delta_e: pixels whose distance is <= this value are considered equal.
    """

    metric: str = "ciede2000"
    delta_e: float = 2.3  # ~ 1 just-noticeable difference


@dataclass
class GeometryTolerance:
    """Displacement tolerance: a differing pixel is forgiven when its colour (or a blend of
    two neighbouring colours, if ``blend``) can be found within ``radius_px`` in the other
    image, and vice-versa.  0 disables."""

    radius_px: int = 1
    delta_e: Optional[float] = None  # None -> colour.delta_e
    blend: bool = True  # also forgive pixels that are a colour blend of neighbours (sub-pixel AA)


@dataclass
class AntiAliasTolerance:
    """Anti-aliasing / sub-pixel rendering tolerance via a Gaussian low-pass.

    A pixel is only reported when it still differs after both images are blurred
    with ``blur_sigma_px``.  0 disables.
    """

    blur_sigma_px: float = 0.0
    delta_e: Optional[float] = None  # None -> colour.delta_e


@dataclass
class AlphaTolerance:
    """Tolerance on the alpha channel (0..255) for RGBA screenshots."""

    tolerance: int = 8


@dataclass
class RegionFilter:
    """Grouping of residual pixels into regions and removal of isolated specks."""

    min_area_px: int = 4
    merge_px: int = 2


@dataclass
class Decision:
    """When does the comparison fail?"""

    fail_pixels: int = 1  # fail when the number of significant pixels >= this value
    fail_ratio: Optional[float] = None  # ... or when pixels / compared_pixels >= this value
    ssim_min: Optional[float] = None  # ... or when SSIM (if computed) < this value


@dataclass
class DiffConfig:
    color: ColorTolerance = field(default_factory=ColorTolerance)
    geometry: GeometryTolerance = field(default_factory=GeometryTolerance)
    antialiasing: AntiAliasTolerance = field(default_factory=AntiAliasTolerance)
    alpha: AlphaTolerance = field(default_factory=AlphaTolerance)
    regions: RegionFilter = field(default_factory=RegionFilter)
    decision: Decision = field(default_factory=Decision)
    mask_padding_px: int = 2  # margin added around every ignore zone
    size_policy: str = "fail"  # fail | pad  (what to do when image sizes differ)
    fast_fail_ratio: float = 0.35  # skip tolerance stages when this share of pixels differs wildly
    extras: List[str] = field(default_factory=list)  # any of: ssim, psnr, phash

    # ------------------------------------------------------------------ helpers
    @property
    def geometry_delta_e(self) -> float:
        return self.geometry.delta_e if self.geometry.delta_e is not None else self.color.delta_e

    @property
    def blur_delta_e(self) -> float:
        return self.antialiasing.delta_e if self.antialiasing.delta_e is not None else self.color.delta_e

    def validate(self) -> "DiffConfig":
        if self.color.metric not in ("ciede2000", "cie76", "rgb"):
            raise ConfigError(f"color.metric must be ciede2000|cie76|rgb, got {self.color.metric!r}")
        if self.color.delta_e < 0:
            raise ConfigError("color.delta_e must be >= 0")
        if self.geometry.radius_px < 0 or self.geometry.radius_px > 8:
            raise ConfigError("geometry.radius_px must be in 0..8")
        if self.antialiasing.blur_sigma_px < 0 or self.antialiasing.blur_sigma_px > 5:
            raise ConfigError("antialiasing.blur_sigma_px must be in 0..5")
        if not 0 <= self.alpha.tolerance <= 255:
            raise ConfigError("alpha.tolerance must be in 0..255")
        if self.regions.min_area_px < 1 or self.regions.merge_px < 0:
            raise ConfigError("regions.min_area_px >= 1 and regions.merge_px >= 0 required")
        if self.decision.fail_pixels < 1:
            raise ConfigError("decision.fail_pixels must be >= 1")
        if self.size_policy not in ("fail", "pad"):
            raise ConfigError("size_policy must be fail|pad")
        bad = [e for e in self.extras if e not in ("ssim", "psnr", "phash")]
        if bad:
            raise ConfigError(f"unknown extras {bad}; allowed: ssim, psnr, phash")
        return self

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "DiffConfig":
        return _build(cls, data or {}, "diff").validate()

    def merged(self, overrides: Optional[Dict[str, Any]]) -> "DiffConfig":
        """Return a copy of this config with a (partial) nested dict applied on top."""
        base = self.to_dict()
        _deep_update(base, overrides or {})
        return DiffConfig.from_dict(base)


def _deep_update(dst: Dict[str, Any], src: Dict[str, Any]) -> None:
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_update(dst[k], v)
        else:
            dst[k] = v


def _build(cls, data: Dict[str, Any], path: str):
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: expected a mapping, got {type(data).__name__}")
    known = {f.name: f for f in fields(cls)}
    unknown = set(data) - set(known)
    if unknown:
        raise ConfigError(f"{path}: unknown key(s) {sorted(unknown)}; allowed: {sorted(known)}")
    kwargs: Dict[str, Any] = {}
    for name, f in known.items():
        if name not in data:
            continue
        val = data[name]
        default = f.default_factory() if f.default_factory is not MISSING else f.default
        if is_dataclass(default):
            kwargs[name] = _build(type(default), val, f"{path}.{name}")
        else:
            kwargs[name] = val
    return cls(**kwargs)
