"""``vrt`` command line.

Exit codes (for CI):  0 = no visual differences,  1 = differences found / baseline missing,  2 = error.

    vrt init                                  write an example config
    vrt baseline --config vrt.config.yaml     capture and store baselines
    vrt test     --config vrt.config.yaml     capture, compare, report, publish CI status
    vrt compare  a.png b.png                  compare two images
    vrt approve  --config vrt.config.yaml     accept the last run's captures as new baselines
    vrt serve | mock-ci                       API / webhook service, mock GitHub+GitLab
    vrt dataset ... | experiments ...         labelled dataset and verification experiments
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import List, Optional

from capture import CaptureError
from diff_engine import ConfigError, write_page_artifacts, write_html_report

from .config import EXAMPLE_CONFIG, apply_overrides, load_config
from .pipeline import EXIT_DIFF, EXIT_ERROR, EXIT_OK, RunOptions, compare_pair, error_summary, run_baseline, run_test
from .profiles import get_profile, profile_dict, profile_names
from .store import BaselineStore

log = logging.getLogger("vrt")


# ------------------------------------------------------------------------------- commands
def cmd_init(a: argparse.Namespace) -> int:
    out = Path(a.out)
    if out.exists() and not a.force:
        print(f"{out} already exists (use --force to overwrite)", file=sys.stderr)
        return EXIT_ERROR
    out.write_text(EXAMPLE_CONFIG)
    print(f"wrote {out}")
    return EXIT_OK


def cmd_baseline(a: argparse.Namespace) -> int:
    cfg = load_config(a.config, a.set)
    res = run_baseline(cfg, RunOptions(only=a.scenario, stability_runs=a.stability_runs), overwrite=not a.missing_only)
    print(f"saved {len(res['saved'])} baseline(s) to {res['baseline_dir']}")
    for s in res["saved"]:
        print(f"  + {s}")
    for e in res["errors"]:
        print(f"  ! {e['page']}: {e['error']}", file=sys.stderr)
    return EXIT_ERROR if res["errors"] else EXIT_OK


def _print_summary(s: dict) -> None:
    print()
    print(f"{'page':44s} {'status':8s} {'sig. px':>9s} {'max dE':>7s} {'ms':>6s}")
    for p in s["pages"]:
        if p["status"] in ("passed", "failed"):
            print(f"{p['page']:44s} {p['status']:8s} {p.get('diff_pixels', 0):9d} {p.get('max_delta_e', 0):7.1f} {p.get('diff_time_ms', 0):6.0f}")
        else:
            print(f"{p['page']:44s} {p['status']:8s} {p.get('error', p.get('note', ''))}")
    print(f"\nverdict: {s['verdict'].upper()}  ({s['passed']}/{s['total']} passed, {s['failed']} changed, {s['missing_baseline']} new, {s['errors']} errors)")


def cmd_test(a: argparse.Namespace) -> int:
    from ci_adapter import make_reporters, publish_all

    cfg = load_config(a.config, a.set)
    if a.report_dir:
        cfg.output_dir = str(Path(a.report_dir).resolve())
    if a.ci:
        cfg.ci.provider = a.ci
    ctx, reporters = (None, []) if a.no_ci else make_reporters(cfg.ci)
    report_url = a.report_url or cfg.ci.artifact_url
    if reporters:
        publish_all(reporters, ctx, None, report_url)  # "pending" status while the suite runs
    try:
        summary = run_test(cfg, RunOptions(only=a.scenario, update_missing=a.update_missing, memory_probe=a.memory, all_artifacts=a.all_artifacts))
    except (ConfigError, CaptureError) as exc:
        if reporters:
            publish_all(reporters, ctx, error_summary(cfg, str(exc)), report_url)
        raise
    except Exception as exc:  # noqa: BLE001
        log.exception("run crashed")
        if reporters:
            publish_all(reporters, ctx, error_summary(cfg, f"{type(exc).__name__}: {exc}"), report_url)
        return EXIT_ERROR
    _print_summary(summary)
    if reporters:
        summary["ci"] = publish_all(reporters, ctx, summary, report_url)
        (Path(cfg.output_dir) / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
        for r in summary["ci"]:
            print(f"[ci] {r['reporter']}: {'ok' if r['ok'] else 'FAILED ' + r.get('error', '')}")
    print(f"report: {Path(cfg.output_dir) / 'index.html'}")
    return int(summary["exit_code"])


def _parse_region(s: str) -> dict:
    try:
        x, y, w, h = (float(v) for v in s.split(","))
    except ValueError:
        raise argparse.ArgumentTypeError(f"--ignore expects x,y,w,h got {s!r}") from None
    return {"x": x, "y": y, "w": w, "h": h}


def cmd_compare(a: argparse.Namespace) -> int:
    base = get_profile(a.profile)
    if a.config:
        base = load_config(a.config).diff
    if a.set:
        base = base.merged(apply_overrides({}, a.set))
    for f in (a.baseline, a.candidate):
        if not Path(f).is_file():
            print(f"file not found: {f}", file=sys.stderr)
            return EXIT_ERROR
    res, mem = compare_pair(a.baseline, a.candidate, base, a.ignore, memory_probe=a.memory, keep_artifacts=bool(a.out))
    verdict = "passed" if res.passed else "failed"
    payload = {"verdict": verdict, **res.to_dict(), "memory_peak_mb": None if mem is None else round(mem, 3)}
    if a.out:
        out = Path(a.out)
        files = write_page_artifacts(out, "compare", res)
        entry = {"page": "compare", "status": verdict, **res.to_dict(), "files": files}
        summary = {"generated_at": "", "config": f"profile {a.profile}", "total": 1, "passed": int(res.passed), "failed": int(not res.passed), "missing_baseline": 0, "errors": 0, "verdict": verdict}
        write_html_report(out, [entry], summary, title="Image comparison")
        payload["report"] = str(out / "index.html")
    if a.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(f"{verdict.upper()}: {res.diff_pixels} significant px ({res.diff_ratio:.5%}), raw {res.raw_diff_pixels} px, max dE {res.max_delta_e:.1f}, {len(res.regions)} region(s), {res.diff_time_ms:.0f} ms")
        for r in res.regions[:5]:
            print(f"  region {r['w']}x{r['h']} at ({r['x']},{r['y']}): {r['area']} px, max dE {r['max_delta_e']}")
        for reason in res.reasons:
            print(f"  - {reason}")
        if "report" in payload:
            print(f"report: {payload['report']}")
    return EXIT_OK if res.passed else EXIT_DIFF


def cmd_approve(a: argparse.Namespace) -> int:
    cfg = load_config(a.config)
    src = Path(a.source or Path(cfg.output_dir) / "candidates")
    if not src.is_dir():
        print(f"no candidates in {src}; run `vrt test` first", file=sys.stderr)
        return EXIT_ERROR
    done = BaselineStore(cfg.baseline_dir).approve_from(src, a.scenario)
    for k in done:
        print(f"approved {k.page_id}")
    print(f"{len(done)} baseline(s) updated in {cfg.baseline_dir}")
    return EXIT_OK


def cmd_profiles(a: argparse.Namespace) -> int:
    for n in profile_names():
        print(f"{n}: {json.dumps(profile_dict(n))}")
    return EXIT_OK


def cmd_serve(a: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run("ci_adapter.server:app", host=a.host, port=a.port, workers=a.workers, log_level="info")
    return EXIT_OK


def cmd_mock_ci(a: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run("ci_adapter.mock_ci:app", host=a.host, port=a.port, log_level="info")
    return EXIT_OK


def cmd_dataset(a: argparse.Namespace) -> int:
    if a.what == "generate":
        from test_dataset.generate import main as gen

        return gen(a.rest)
    from test_dataset.extras import main as extras

    return extras(a.rest)


def cmd_experiments(a: argparse.Namespace) -> int:
    from experiments.run import main as run

    return run([a.what] + a.rest)


# ------------------------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="vrt", description="Visual regression testing: capture, tolerant pixel-diff, reports, CI status.")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="write an example config")
    s.add_argument("--out", default="vrt.config.yaml")
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("baseline", help="capture and store baselines")
    s.add_argument("-c", "--config", required=True)
    s.add_argument("--scenario", action="append")
    s.add_argument("--missing-only", action="store_true", help="only create baselines that do not exist yet")
    s.add_argument("--stability-runs", type=int, default=1, help="N>1: reload N times and auto-mask volatile regions")
    s.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
    s.set_defaults(func=cmd_baseline)

    s = sub.add_parser("test", help="capture + compare + report (+ CI status); exit 0/1/2")
    s.add_argument("-c", "--config", required=True)
    s.add_argument("--scenario", action="append")
    s.add_argument("--update-missing", action="store_true", help="store captures without baseline as new baselines")
    s.add_argument("--ci", choices=["auto", "github", "gitlab", "webhook", "none"], help="override ci.provider")
    s.add_argument("--no-ci", action="store_true", help="do not publish any CI status")
    s.add_argument("--report-dir")
    s.add_argument("--report-url", help="public URL of the report (linked from the status / comment)")
    s.add_argument("--all-artifacts", action="store_true", help="write overlay images also for passed pages")
    s.add_argument("--memory", action=argparse.BooleanOptionalAction, default=None, help="measure memory_peak_mb (extra traced run per page)")
    s.add_argument("--set", action="append", default=[], metavar="KEY=VALUE", help="override config values, e.g. diff.color.delta_e=3")
    s.set_defaults(func=cmd_test)

    s = sub.add_parser("compare", help="compare two images")
    s.add_argument("baseline")
    s.add_argument("candidate")
    s.add_argument("--profile", default="balanced", choices=profile_names())
    s.add_argument("-c", "--config", help="take thresholds from a project config")
    s.add_argument("--set", action="append", default=[], metavar="KEY=VALUE", help="override thresholds, e.g. color.delta_e=3")
    s.add_argument("--ignore", action="append", default=[], type=_parse_region, metavar="X,Y,W,H", help="ignored zone (repeatable)")
    s.add_argument("--out", help="write overlay / side-by-side / JSON / HTML report here")
    s.add_argument("--memory", action="store_true")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_compare)

    s = sub.add_parser("approve", help="accept the captures of the last run as new baselines")
    s.add_argument("-c", "--config", required=True)
    s.add_argument("--scenario", action="append")
    s.add_argument("--source", help="candidates dir (default: <output_dir>/candidates)")
    s.set_defaults(func=cmd_approve)

    s = sub.add_parser("profiles", help="list threshold profiles")
    s.set_defaults(func=cmd_profiles)

    s = sub.add_parser("serve", help="run the API / webhook service")
    s.add_argument("--host", default="0.0.0.0")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--workers", type=int, default=1)
    s.set_defaults(func=cmd_serve)

    s = sub.add_parser("mock-ci", help="run the mock GitHub/GitLab server (with merge protection)")
    s.add_argument("--host", default="0.0.0.0")
    s.add_argument("--port", type=int, default=8099)
    s.set_defaults(func=cmd_mock_ci)

    s = sub.add_parser("dataset", help="build the labelled reference dataset")
    s.add_argument("what", choices=["generate", "extras"], help="generate: main dataset; extras: holdout-noise + sensitivity sets")
    s.add_argument("rest", nargs=argparse.REMAINDER)
    s.set_defaults(func=cmd_dataset)

    s = sub.add_parser("experiments", help="verification experiments (evaluate / calibrate / analyze / loadtest / stability / plots / all)")
    s.add_argument("what", choices=["evaluate", "calibrate", "analyze", "loadtest", "stability", "plots", "all"])
    s.add_argument("rest", nargs=argparse.REMAINDER)
    s.set_defaults(func=cmd_experiments)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(name)s: %(message)s", stream=sys.stderr)
    try:
        return int(args.func(args))
    except (ConfigError, CaptureError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
