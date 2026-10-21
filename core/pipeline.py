"""Suite pipeline:  capture -> compare -> report  (CI publishing lives in ``ci_adapter``).

``run_test``     captures every scenario x browser x viewport, compares with the stored baseline,
                 writes ``summary.json`` / ``index.html`` / ``junit.xml`` / metrics and returns the summary.
``run_baseline`` captures and stores baselines (``vrt baseline``).
``compare_pair`` is the single-pair building block shared by the CLI, the API service and the experiments.
"""
from __future__ import annotations

import dataclasses
import itertools
import json
import logging
import shutil
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from capture import CaptureError, CaptureResult, CaptureSession, CaptureSpec, discover_volatile_regions
from diff_engine import ConfigError, DiffConfig, DiffResult, compare_images, write_html_report, write_page_artifacts
from metrics import Metrics, measure_peak_memory

from .config import ProjectConfig, Scenario, Viewport
from .profiles import get_profile
from .store import BaselineStore, PageKey

log = logging.getLogger("vrt")

EXIT_OK, EXIT_DIFF, EXIT_ERROR = 0, 1, 2
SUMMARY_SCHEMA = "vrt.summary/1"


# ------------------------------------------------------------------------------------ model
@dataclass
class PageSpec:
    key: PageKey
    spec: CaptureSpec
    diff: DiffConfig


@dataclass
class RunOptions:
    only: Optional[List[str]] = None  # restrict to these scenario names
    update_missing: bool = False  # store candidates of pages without baseline as new baselines
    memory_probe: Optional[bool] = None  # override config.metrics.memory_probe
    stability_runs: int = 1  # >1: auto-discover volatile regions when capturing baselines
    all_artifacts: bool = False  # write overlay/side-by-side PNGs also for passed pages
    metrics: Optional[Metrics] = None


def expand_pages(cfg: ProjectConfig, only: Optional[Sequence[str]] = None) -> List[PageSpec]:
    if not cfg.scenarios:
        raise ConfigError("config has no scenarios")
    known = {s.name for s in cfg.scenarios}
    if only:
        missing = [n for n in only if n not in known]
        if missing:
            raise ConfigError(f"unknown scenario(s) {missing}; known: {sorted(known)}")
    vps = {v.name: v for v in cfg.capture.viewports}
    pages: List[PageSpec] = []
    for sc in cfg.scenarios:
        if only and sc.name not in only:
            continue
        for browser in sc.browsers or cfg.capture.browsers:
            for vn in sc.viewports or list(vps):
                vp = vps[vn]
                spec = CaptureSpec(
                    name=sc.name,
                    url=cfg.scenario_url(sc),
                    viewport_width=vp.width,
                    viewport_height=vp.height,
                    device_scale_factor=cfg.capture.device_scale_factor,
                    full_page=cfg.capture.full_page if sc.full_page is None else sc.full_page,
                    locale=cfg.capture.locale,
                    timezone=cfg.capture.timezone,
                    color_scheme=cfg.capture.color_scheme,
                    mask_selectors=list(sc.mask_selectors),
                    mask_regions=list(sc.mask_regions),
                    extra_css=sc.extra_css,
                    pre_capture_js=sc.pre_capture_js,
                    wait_for_selector=sc.wait_for_selector,
                    stabilize=cfg.capture.stabilize,
                )
                pages.append(PageSpec(PageKey(sc.name, browser, vn), spec, cfg.diff_for(sc)))
    return pages


# ---------------------------------------------------------------------------------- capture
def capture_pages(
    cfg: ProjectConfig, pages: Sequence[PageSpec], stability_runs: int = 1, metrics: Optional[Metrics] = None
) -> Dict[PageKey, Tuple[Optional[CaptureResult], Optional[str]]]:
    """Capture all pages (one browser instance per engine). Failures are returned, never raised."""
    out: Dict[PageKey, Tuple[Optional[CaptureResult], Optional[str]]] = {}
    by_browser: Dict[str, List[PageSpec]] = {}
    for p in pages:
        by_browser.setdefault(p.key.browser, []).append(p)
    for browser, items in by_browser.items():
        try:
            with CaptureSession(browser, launch_args=cfg.capture.launch_args or None) as sess:
                for p in items:
                    t0 = time.perf_counter()
                    try:
                        res = sess.capture(p.spec)
                        if stability_runs > 1:
                            loose = dataclasses.replace(
                                p.spec, stabilize=dataclasses.replace(p.spec.stabilize, freeze_time=None, max_attempts=1)
                            )
                            frames = [sess.capture(loose).png for _ in range(stability_runs)]
                            res.meta["auto_masks"] = discover_volatile_regions(frames)
                        out[p.key] = (res, None)
                        if metrics:
                            metrics.observe_capture((time.perf_counter() - t0) * 1000, stable=bool(res.meta.get("stable", True)))
                        log.info("captured %s (%.0f ms, stable=%s)", p.key.page_id, (time.perf_counter() - t0) * 1000, res.meta.get("stable"))
                    except Exception as exc:  # noqa: BLE001 - report per page, keep going
                        out[p.key] = (None, f"{type(exc).__name__}: {exc}")
                        if metrics:
                            metrics.observe_capture(0, error=True)
                        log.error("capture failed for %s: %s", p.key.page_id, exc)
        except CaptureError as exc:
            for p in items:
                out[p.key] = (None, str(exc))
                if metrics:
                    metrics.observe_capture(0, error=True)
            log.error("browser %s unavailable: %s", browser, exc)
    return out


# ---------------------------------------------------------------------------------- compare
def compare_pair(
    baseline: Any,
    candidate: Any,
    diff_cfg: Optional[DiffConfig] = None,
    regions: Optional[Sequence[Dict[str, Any]]] = None,
    memory_probe: bool = False,
    keep_artifacts: bool = False,
    metrics: Optional[Metrics] = None,
    algorithm: str = "vrt",
) -> Tuple[DiffResult, Optional[float]]:
    """Compare two images; returns the result and (optionally) the traced peak memory in MB."""
    cfg = diff_cfg or get_profile("balanced")
    res = compare_images(baseline, candidate, cfg, regions, keep_artifacts=keep_artifacts)
    mem: Optional[float] = None
    if memory_probe:
        mem = measure_peak_memory(lambda: compare_images(baseline, candidate, cfg, regions))
    if metrics:
        metrics.observe_comparison(
            result="passed" if res.passed else "failed",
            time_ms=res.diff_time_ms,
            memory_peak_mb=mem,
            diff_pixels=res.diff_pixels,
            algorithm=algorithm,
        )
    return res, mem


def _ignore_regions(cand: CaptureResult, base_meta: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Ignored zones = selectors/static regions of the candidate + those recorded with the baseline
    (+ volatile regions discovered when the baseline was captured)."""
    out: List[Dict[str, Any]] = []
    for src in (cand.meta.get("regions", []), base_meta.get("regions", []), base_meta.get("auto_masks", [])):
        for r in src:
            out.append({k: r[k] for k in ("x", "y", "w", "h")})
    return out


# ---------------------------------------------------------------------------------- baselines
def run_baseline(cfg: ProjectConfig, opts: RunOptions, overwrite: bool = True) -> Dict[str, Any]:
    pages = expand_pages(cfg, opts.only)
    store = BaselineStore(cfg.baseline_dir)
    todo = [p for p in pages if overwrite or not store.exists(p.key)]
    caps = capture_pages(cfg, todo, opts.stability_runs, opts.metrics)
    saved, errors = [], []
    for p in todo:
        res, err = caps[p.key]
        if res is None:
            errors.append({"page": p.key.page_id, "error": err})
            continue
        store.save(p.key, res.png, res.meta)
        saved.append(p.key.page_id)
    return {"saved": saved, "skipped": [p.key.page_id for p in pages if p not in todo], "errors": errors, "baseline_dir": str(store.root)}


# --------------------------------------------------------------------------------- test run
def run_test(cfg: ProjectConfig, opts: Optional[RunOptions] = None) -> Dict[str, Any]:
    opts = opts or RunOptions()
    metrics = opts.metrics or Metrics()
    memory = cfg.metrics.memory_probe if opts.memory_probe is None else opts.memory_probe
    t_wall = time.perf_counter()
    pages = expand_pages(cfg, opts.only)
    store = BaselineStore(cfg.baseline_dir)
    out_dir = Path(cfg.output_dir)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    (out_dir / "pages").mkdir(parents=True)
    cand_store = BaselineStore(out_dir / "candidates")

    t_cap = time.perf_counter()
    caps = capture_pages(cfg, pages, 1, metrics)
    capture_ms = (time.perf_counter() - t_cap) * 1000

    results: List[Dict[str, Any]] = []
    diff_ms = 0.0
    for p in pages:
        key = p.key
        cap, err = caps[key]
        entry: Dict[str, Any] = {
            "page": key.page_id,
            "scenario": key.scenario,
            "browser": key.browser,
            "viewport": key.viewport,
            "files": {},
        }
        if cap is None:
            entry.update(status="error", error=err)
            results.append(entry)
            continue
        cand_store.save(key, cap.png, cap.meta)
        entry["files"]["candidate"] = f"candidates/{key.rel.as_posix()}"
        entry["capture"] = {k: cap.meta.get(k) for k in ("stable", "frames_taken", "timings_ms", "browser_version", "image_size")}
        if not store.exists(key):
            if opts.update_missing or cfg.on_missing_baseline == "create":
                store.save(key, cap.png, cap.meta)
                entry["status"] = "passed"
                entry["note"] = "baseline created"
            else:
                entry["status"] = "new"
                entry["note"] = "no baseline stored"
            results.append(entry)
            continue
        try:
            base_png, base_meta = store.load(key)
            regions = _ignore_regions(cap, base_meta)
            res, mem = compare_pair(base_png, cap.png, p.diff, regions, memory_probe=memory, keep_artifacts=True, metrics=metrics)
        except Exception as exc:  # noqa: BLE001
            entry.update(status="error", error=f"{type(exc).__name__}: {exc}")
            results.append(entry)
            continue
        diff_ms += res.diff_time_ms
        passed = res.passed
        if passed and not opts.all_artifacts:
            res.artifacts = {}
        files = write_page_artifacts(out_dir / "pages", key.slug, res, extra={"page_id": key.page_id})
        entry["files"].update({k: f"pages/{v}" for k, v in files.items()})
        entry.update({k: v for k, v in res.to_dict().items()})
        entry["status"] = "passed" if passed else "failed"
        entry["memory_peak_mb"] = None if mem is None else round(mem, 3)
        entry["ignored_regions"] = len(regions)
        results.append(entry)
        log.info("%s: %s (%d px, %.0f ms)", key.page_id, entry["status"], res.diff_pixels, res.diff_time_ms)

    consistency = _cross_browser(cfg, pages, caps) if cfg.cross_browser else []
    summary = build_summary(cfg, results, consistency, capture_ms, diff_ms, time.perf_counter() - t_wall)
    write_outputs(cfg, summary, out_dir, metrics)
    return summary


def _cross_browser(cfg: ProjectConfig, pages: Sequence[PageSpec], caps) -> List[Dict[str, Any]]:
    """Informational: how much do the engines disagree on the same page? (never gates the build)"""
    lenient = get_profile("lenient")
    groups: Dict[Tuple[str, str], List[PageSpec]] = {}
    for p in pages:
        groups.setdefault((p.key.scenario, p.key.viewport), []).append(p)
    rows: List[Dict[str, Any]] = []
    for (scenario, vp), items in groups.items():
        for a, b in itertools.combinations(items, 2):
            ca, cb = caps[a.key][0], caps[b.key][0]
            if ca is None or cb is None:
                continue
            regions = _ignore_regions(ca, {}) + _ignore_regions(cb, {})
            res = compare_images(ca.png, cb.png, lenient, regions)
            rows.append(
                {
                    "scenario": scenario,
                    "viewport": vp,
                    "browsers": [a.key.browser, b.key.browser],
                    "diff_ratio": round(res.diff_ratio, 6),
                    "significant_pixels": res.diff_pixels,
                    "size_mismatch": res.size_mismatch,
                }
            )
    return rows


def build_summary(
    cfg: ProjectConfig,
    results: List[Dict[str, Any]],
    consistency: List[Dict[str, Any]],
    capture_ms: float,
    diff_ms: float,
    wall_s: float,
) -> Dict[str, Any]:
    n = lambda s: sum(1 for r in results if r["status"] == s)  # noqa: E731
    errors, failed, new = n("error"), n("failed"), n("new")
    new_fails = new > 0 and cfg.on_missing_baseline == "fail"
    if errors:
        verdict, code = "error", EXIT_ERROR
    elif failed or new_fails:
        verdict, code = "failed", EXIT_DIFF
    else:
        verdict, code = "passed", EXIT_OK
    return {
        "schema": SUMMARY_SCHEMA,
        "project": cfg.name,
        "config": cfg.source or "",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "verdict": verdict,
        "exit_code": code,
        "total": len(results),
        "passed": n("passed"),
        "failed": failed,
        "missing_baseline": new,
        "errors": errors,
        "profile": cfg.diff_profile,
        "thresholds": cfg.diff.to_dict(),
        "timing": {"capture_ms": round(capture_ms, 1), "diff_ms": round(diff_ms, 1), "wall_s": round(wall_s, 2)},
        "pages": results,
        "consistency": consistency,
    }


def error_summary(cfg: ProjectConfig, message: str) -> Dict[str, Any]:
    """Summary for a run that crashed before producing results (still published to CI as ``error``)."""
    s = build_summary(cfg, [{"page": "(run)", "scenario": "(run)", "browser": "-", "viewport": "-", "status": "error", "error": message, "files": {}}], [], 0, 0, 0)
    return s


def _junit(summary: Dict[str, Any]) -> ET.ElementTree:
    suite = ET.Element(
        "testsuite",
        name=f"vrt:{summary['project']}",
        tests=str(summary["total"]),
        failures=str(summary["failed"] + summary["missing_baseline"]),
        errors=str(summary["errors"]),
        time=str(summary["timing"]["wall_s"]),
    )
    for r in summary["pages"]:
        case = ET.SubElement(suite, "testcase", classname=r["scenario"], name=f"{r['browser']}/{r['viewport']}")
        if r["status"] == "failed":
            msg = "; ".join(r.get("reasons", [])) or "visual difference"
            ET.SubElement(case, "failure", message=msg).text = json.dumps({"diff_pixels": r.get("diff_pixels"), "regions": r.get("regions", [])[:10]})
        elif r["status"] == "new":
            ET.SubElement(case, "failure", message="no baseline stored (run `vrt approve`)")
        elif r["status"] == "error":
            ET.SubElement(case, "error", message=str(r.get("error", "error")))
    return ET.ElementTree(suite)


def write_outputs(cfg: ProjectConfig, summary: Dict[str, Any], out_dir: Path, metrics: Metrics) -> None:
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    write_html_report(out_dir, summary["pages"], {**summary, "missing_baseline": summary["missing_baseline"]}, title=f"Visual regression report - {summary['project']}")
    _junit(summary).write(out_dir / "junit.xml", encoding="utf-8", xml_declaration=True)
    if cfg.metrics.prometheus_file:
        metrics.export_prometheus(out_dir / cfg.metrics.prometheus_file)
    if cfg.metrics.json_file:
        metrics.export_json(out_dir / cfg.metrics.json_file)
