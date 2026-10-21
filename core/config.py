"""Project configuration (YAML / JSON): scenarios, capture settings, thresholds, CI and metrics.

Minimal example (see ``examples/vrt.config.yaml`` for a commented one)::

    version: 1
    base_url: http://localhost:8080
    diff: {profile: balanced}
    scenarios:
      - {name: home, path: /index.html, mask_selectors: ["[data-vrt-ignore]"]}
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import MISSING, asdict, dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from capture.stabilize import Stabilize
from diff_engine.config import ConfigError, DiffConfig

from .profiles import get_profile

SUPPORTED_BROWSERS = ("chromium", "firefox", "webkit")
ON_MISSING = ("fail", "pass", "create")


@dataclass
class Viewport:
    name: str = "desktop"
    width: int = 1280
    height: int = 720


@dataclass
class CaptureCfg:
    browsers: List[str] = field(default_factory=lambda: ["chromium"])
    viewports: List[Viewport] = field(default_factory=lambda: [Viewport()])
    device_scale_factor: float = 1.0
    full_page: bool = False
    locale: str = "en-US"
    timezone: str = "UTC"
    color_scheme: str = "light"
    launch_args: List[str] = field(default_factory=list)  # e.g. ["--font-render-hinting=none"]
    stabilize: Stabilize = field(default_factory=Stabilize)


@dataclass
class Scenario:
    name: str
    url: Optional[str] = None  # absolute URL / file path ...
    path: Optional[str] = None  # ... or a path relative to base_url
    mask_selectors: List[str] = field(default_factory=list)
    mask_regions: List[Dict[str, float]] = field(default_factory=list)
    extra_css: str = ""
    pre_capture_js: str = ""
    wait_for_selector: Optional[str] = None
    full_page: Optional[bool] = None
    viewports: Optional[List[str]] = None  # names from capture.viewports (default: all)
    browsers: Optional[List[str]] = None
    diff: Dict[str, Any] = field(default_factory=dict)  # per-scenario threshold overrides


@dataclass
class CiCfg:
    provider: str = "none"  # none | github | gitlab | webhook | auto
    context: str = "vrt/visual-regression"  # name of the commit status / check run
    api_url: Optional[str] = None  # GitHub/GitLab API root (default: public API; point to mock-ci in demos)
    token_env: Optional[str] = None  # env var holding the token (default GITHUB_TOKEN / GITLAB_TOKEN)
    comment: bool = True  # post / update a PR (MR) comment
    check_run: bool = True  # GitHub: also create a check run with a summary
    artifact_url: Optional[str] = None  # where the HTML report is published (linked from status / comment)
    webhook_url: Optional[str] = None
    webhook_secret_env: str = "VRT_WEBHOOK_SECRET"


@dataclass
class MetricsCfg:
    memory_probe: bool = True
    prometheus_file: Optional[str] = "metrics.prom"  # relative to the report dir
    json_file: Optional[str] = "metrics.json"


@dataclass
class ProjectConfig:
    version: int = 1
    name: str = "vrt-project"
    base_url: str = ""
    baseline_dir: str = ".vrt/baselines"
    output_dir: str = ".vrt/report"
    on_missing_baseline: str = "fail"  # fail | pass | create
    cross_browser: bool = False  # report cross-browser consistency (informational)
    capture: CaptureCfg = field(default_factory=CaptureCfg)
    diff_profile: str = "balanced"
    diff: DiffConfig = field(default_factory=lambda: get_profile("balanced"))
    scenarios: List[Scenario] = field(default_factory=list)
    ci: CiCfg = field(default_factory=CiCfg)
    metrics: MetricsCfg = field(default_factory=MetricsCfg)
    source: Optional[str] = None  # path the config was loaded from

    # ------------------------------------------------------------------------------ helpers
    def scenario_url(self, sc: Scenario) -> str:
        if sc.url:
            return sc.url
        if sc.path is None:
            raise ConfigError(f"scenario {sc.name!r}: set either url or path")
        if not self.base_url:
            raise ConfigError(f"scenario {sc.name!r} uses path but base_url is empty")
        return self.base_url.rstrip("/") + "/" + sc.path.lstrip("/")

    def diff_for(self, sc: Scenario) -> DiffConfig:
        return self.diff.merged(sc.diff) if sc.diff else self.diff

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["diff"] = self.diff.to_dict()
        return d


# ---------------------------------------------------------------------------------- loading
_ENV = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def _expand_env(value: Any) -> Any:
    if isinstance(value, str):
        return _ENV.sub(lambda m: os.environ.get(m.group(1), m.group(2) if m.group(2) is not None else ""), value)
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    return value


def _build(cls, data: Any, path: str):
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
        default = f.default_factory() if f.default_factory is not MISSING else f.default  # type: ignore[misc]
        if is_dataclass(default):
            kwargs[name] = _build(type(default), val, f"{path}.{name}")
        elif name == "viewports" and cls is CaptureCfg:
            kwargs[name] = [_build(Viewport, v, f"{path}.viewports[{i}]") for i, v in enumerate(val)]
        else:
            kwargs[name] = val
    return cls(**kwargs)


def parse_config(data: Dict[str, Any], source: Optional[str] = None) -> ProjectConfig:
    data = _expand_env(dict(data or {}))
    scen_raw = data.pop("scenarios", [])
    diff_raw = data.pop("diff", {}) or {}
    if not isinstance(diff_raw, dict):
        raise ConfigError("diff: expected a mapping")
    diff_raw = dict(diff_raw)
    profile = diff_raw.pop("profile", "balanced")
    cfg = _build(ProjectConfig, data, "config")
    cfg.diff_profile = profile
    cfg.diff = get_profile(profile).merged(diff_raw)
    if not isinstance(scen_raw, list):
        raise ConfigError("scenarios: expected a list")
    names = set()
    for i, s in enumerate(scen_raw):
        sc = _build(Scenario, s, f"scenarios[{i}]")
        if not re.fullmatch(r"[A-Za-z0-9_.\-]+", sc.name or ""):
            raise ConfigError(f"scenarios[{i}].name must match [A-Za-z0-9_.-]+, got {sc.name!r}")
        if sc.name in names:
            raise ConfigError(f"duplicate scenario name {sc.name!r}")
        names.add(sc.name)
        if sc.diff:
            cfg.diff.merged(sc.diff)  # validate overrides eagerly
        cfg.scenarios.append(sc)
    _validate(cfg)
    cfg.source = source
    return cfg


def _validate(cfg: ProjectConfig) -> None:
    if cfg.version != 1:
        raise ConfigError(f"unsupported config version {cfg.version}")
    if cfg.on_missing_baseline not in ON_MISSING:
        raise ConfigError(f"on_missing_baseline must be one of {ON_MISSING}")
    for b in cfg.capture.browsers:
        if b not in SUPPORTED_BROWSERS:
            raise ConfigError(f"capture.browsers: unsupported {b!r}; choose from {SUPPORTED_BROWSERS}")
    if not cfg.capture.viewports:
        raise ConfigError("capture.viewports must not be empty")
    vp_names = [v.name for v in cfg.capture.viewports]
    if len(set(vp_names)) != len(vp_names):
        raise ConfigError("capture.viewports names must be unique")
    for v in cfg.capture.viewports:
        if not (16 <= v.width <= 8192 and 16 <= v.height <= 8192):
            raise ConfigError(f"viewport {v.name}: size out of range")
    for sc in cfg.scenarios:
        for vn in sc.viewports or []:
            if vn not in vp_names:
                raise ConfigError(f"scenario {sc.name}: unknown viewport {vn!r}")
        for b in sc.browsers or []:
            if b not in SUPPORTED_BROWSERS:
                raise ConfigError(f"scenario {sc.name}: unsupported browser {b!r}")
    if cfg.ci.provider not in ("none", "github", "gitlab", "webhook", "auto"):
        raise ConfigError("ci.provider must be none|github|gitlab|webhook|auto")


def load_config(path: str | Path, overrides: Optional[List[str]] = None) -> ProjectConfig:
    """Load a YAML/JSON project config; ``overrides`` are ``a.b.c=value`` assignments applied on top."""
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"config not found: {p}")
    text = p.read_text(encoding="utf-8")
    try:
        data = json.loads(text) if p.suffix == ".json" else yaml.safe_load(text)
    except (yaml.YAMLError, json.JSONDecodeError) as exc:
        raise ConfigError(f"cannot parse {p}: {exc}") from exc
    if overrides:
        data = apply_overrides(dict(data or {}), overrides)
    cfg = parse_config(data or {}, source=str(p))
    # relative paths are resolved against the config file's directory
    base = p.resolve().parent
    for attr in ("baseline_dir", "output_dir"):
        v = Path(getattr(cfg, attr))
        if not v.is_absolute():
            setattr(cfg, attr, str((base / v).resolve()))
    return cfg


def apply_overrides(data: Dict[str, Any], assignments: List[str]) -> Dict[str, Any]:
    """Apply ``a.b.c=value`` command-line overrides (value parsed as YAML) to a raw config dict."""
    for a in assignments:
        if "=" not in a:
            raise ConfigError(f"--set expects key=value, got {a!r}")
        key, raw = a.split("=", 1)
        try:
            val = yaml.safe_load(raw)
        except yaml.YAMLError:
            val = raw
        cur = data
        parts = key.strip().split(".")
        for part in parts[:-1]:
            cur = cur.setdefault(part, {})
        cur[parts[-1]] = val
    return data


EXAMPLE_CONFIG = """\
version: 1
name: my-app
base_url: http://localhost:8080          # ${VRT_BASE_URL:-http://localhost:8080} is also supported
baseline_dir: .vrt/baselines
output_dir: .vrt/report
on_missing_baseline: fail                # fail | pass | create

capture:
  browsers: [chromium]                   # chromium | firefox | webkit (per-browser baselines)
  viewports:
    - {name: desktop, width: 1280, height: 720}
    - {name: mobile,  width: 390,  height: 844}
  stabilize:                             # render-stability controls
    freeze_time: "2026-01-01T12:00:00Z"
    max_attempts: 5                      # frames taken until two consecutive frames are identical

diff:
  profile: balanced                      # strict | balanced | lenient (calibrated, see results/)
  color: {delta_e: 2.3}                  # CIEDE2000 tolerance (2.3 ~ 1 just-noticeable difference)
  regions: {min_area_px: 8, merge_px: 3}      # values of the calibrated profile; override only with a reason

scenarios:
  - name: home
    path: /index.html
    mask_selectors: ["[data-vrt-ignore]", ".ad-banner"]     # dynamic content -> ignored zones
    mask_regions: [{x: 0, y: 0, w: 120, h: 24}]              # static ignored zones (px)
  - name: pricing
    path: /pricing.html
    diff: {color: {delta_e: 3.0}}                            # per-scenario threshold override

ci:
  provider: auto                         # github | gitlab | webhook | none (auto: detect from env)
  context: vrt/visual-regression
  comment: true
metrics:
  prometheus_file: metrics.prom
  json_file: metrics.json
"""
