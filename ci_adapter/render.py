"""Human-readable renderings of a run summary: status text, check-run body, PR comment."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

MARKER = "<!-- vrt-report -->"


def verdict_state(summary: Dict[str, Any]) -> str:
    """``success`` | ``failure`` | ``error`` (GitHub vocabulary; GitLab names are mapped by its reporter)."""
    return {"passed": "success", "failed": "failure", "error": "error"}[summary["verdict"]]


def status_description(summary: Dict[str, Any]) -> str:
    total, failed, new, err = summary["total"], summary["failed"], summary["missing_baseline"], summary["errors"]
    if summary["verdict"] == "passed":
        return f"All {total} screenshot(s) match the baseline"[:140]
    parts: List[str] = []
    if failed:
        parts.append(f"{failed} changed")
    if new:
        parts.append(f"{new} new")
    if err:
        parts.append(f"{err} error(s)")
    return f"{', '.join(parts)} of {total} screenshot(s)"[:140]


def check_run_output(summary: Dict[str, Any], report_url: Optional[str]) -> Dict[str, Any]:
    title = status_description(summary)
    lines = [
        f"**Verdict:** `{summary['verdict']}` &middot; profile `{summary['profile']}`",
        "",
        f"| total | passed | changed | new | errors |",
        f"|---|---|---|---|---|",
        f"| {summary['total']} | {summary['passed']} | {summary['failed']} | {summary['missing_baseline']} | {summary['errors']} |",
    ]
    if report_url:
        lines += ["", f"[Open the visual report]({report_url})"]
    return {"title": title, "summary": "\n".join(lines)}


def comment_markdown(summary: Dict[str, Any], report_url: Optional[str] = None, run_url: Optional[str] = None) -> str:
    icon = {"passed": "✅", "failed": "❌", "error": "⚠️"}[summary["verdict"]]
    out = [
        MARKER,
        f"### {icon} Visual regression: {status_description(summary)}",
        "",
        f"`{summary['project']}` · profile `{summary['profile']}` · {summary['timing']['wall_s']} s",
    ]
    bad = [p for p in summary["pages"] if p["status"] in ("failed", "new", "error")]
    if bad:
        out += ["", "| page | status | significant px | max ΔE | largest region |", "|---|---|---:|---:|---|"]
        for p in bad[:20]:
            if p["status"] == "failed":
                reg = p["regions"][0] if p.get("regions") else None
                reg_s = f"{reg['w']}×{reg['h']} at ({reg['x']}, {reg['y']})" if reg else "-"
                out.append(f"| `{p['page']}` | changed | {p['diff_pixels']} | {p['max_delta_e']:.1f} | {reg_s} |")
            elif p["status"] == "new":
                out.append(f"| `{p['page']}` | new (no baseline) | - | - | - |")
            else:
                out.append(f"| `{p['page']}` | error | - | - | {str(p.get('error', ''))[:80]} |")
        if len(bad) > 20:
            out.append(f"\n…and {len(bad) - 20} more.")
        out += ["", "If the change is intended, run `vrt approve` and commit the updated baselines."]
    else:
        out += ["", f"All {summary['total']} screenshot(s) are within the configured tolerances."]
    links = []
    if report_url:
        links.append(f"[Visual report]({report_url})")
    if run_url:
        links.append(f"[CI run]({run_url})")
    if links:
        out += ["", " · ".join(links)]
    return "\n".join(out)
