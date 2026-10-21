import json
import shutil
from pathlib import Path

import httpx
import numpy as np
import pytest
import yaml

from core.cli import main

from conftest import make_ui, save_png

HTML = """<!doctype html><meta charset=utf-8><style>body{margin:0;font:18px sans-serif}
.hero{height:120px;background:%(color)s;color:#fff;padding:20px}</style>
<div class=hero><h1>Welcome</h1></div><p>Static paragraph of text for the page.</p><span id=t data-vrt-ignore>12:00</span>"""


def test_compare_exit_codes(tmp_path, capsys):
    a = save_png(make_ui(), tmp_path / "a.png")
    b = make_ui()
    b[40:100, 16:150] = (0, 0, 0)
    bp = save_png(b, tmp_path / "b.png")
    assert main(["compare", str(a), str(a)]) == 0
    assert main(["compare", str(a), str(bp), "--out", str(tmp_path / "rep")]) == 1
    assert (tmp_path / "rep" / "index.html").is_file() and (tmp_path / "rep" / "compare.json").is_file()
    assert main(["compare", str(a), str(tmp_path / "nope.png")]) == 2
    assert main(["compare", str(a), str(bp), "--ignore", "0,0,320,200"]) == 0  # everything ignored
    assert main(["compare", str(a), str(bp), "--set", "decision.fail_pixels=999999", "--json"]) == 0
    out = capsys.readouterr().out
    assert '"verdict": "passed"' in out


def test_bad_config_exits_2(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("version: 1\nnonsense: true\nscenarios: []\n")
    assert main(["test", "-c", str(p), "--no-ci"]) == 2
    assert main(["test", "-c", str(tmp_path / "missing.yaml")]) == 2


@pytest.fixture()
def project(tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    page = site / "index.html"
    page.write_text(HTML % {"color": "#2563eb"})
    cfg = {"version": 1, "name": "e2e", "baseline_dir": "base", "output_dir": "out",
           "capture": {"viewports": [{"name": "desktop", "width": 600, "height": 360}]},
           "diff": {"profile": "balanced"},
           "scenarios": [{"name": "home", "url": str(page), "mask_selectors": ["[data-vrt-ignore]"]}]}
    cp = tmp_path / "vrt.yaml"
    cp.write_text(yaml.safe_dump(cfg))
    return cp, page


@pytest.mark.usefixtures("need_browser")
def test_end_to_end_baseline_test_fail_approve(project, tmp_path):
    cp, page = project
    assert main(["test", "-c", str(cp), "--no-ci"]) == 1  # no baseline yet -> blocking "new"
    assert main(["baseline", "-c", str(cp)]) == 0
    assert main(["test", "-c", str(cp), "--no-ci"]) == 0
    summary = json.loads((tmp_path / "out" / "summary.json").read_text())
    assert summary["verdict"] == "passed" and summary["pages"][0]["diff_pixels"] == 0
    assert "memory_peak_mb" in summary["pages"][0]

    page.write_text(HTML % {"color": "#dc2626"})  # a real regression
    assert main(["test", "-c", str(cp), "--no-ci"]) == 1
    summary = json.loads((tmp_path / "out" / "summary.json").read_text())
    pg = summary["pages"][0]
    assert pg["status"] == "failed" and pg["diff_pixels"] > 5000 and pg["regions"]
    for f in ("index.html", "junit.xml", "metrics.prom", "metrics.json"):
        assert (tmp_path / "out" / f).is_file(), f
    assert (tmp_path / "out" / pg["files"]["side_by_side"]).is_file()
    assert "vrt_diff_time_ms" in (tmp_path / "out" / "metrics.prom").read_text()

    assert main(["approve", "-c", str(cp)]) == 0
    assert main(["test", "-c", str(cp), "--no-ci"]) == 0


@pytest.mark.usefixtures("need_browser")
def test_ci_integration_blocks_merge_then_unblocks(project, mock_ci, monkeypatch, tmp_path):
    cp, page = project
    httpx.post(f"{mock_ci}/__reset")
    sha = "9" * 40
    pr = httpx.post(f"{mock_ci}/repos/acme/web/pulls", json={"head": sha}, headers={"Authorization": "Bearer t"}).json()["number"]
    env = {"VRT_CI_PROVIDER": "github", "VRT_REPO": "acme/web", "VRT_SHA": sha, "VRT_PR": str(pr), "VRT_CI_API_URL": mock_ci, "GITHUB_TOKEN": "t"}
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    merge = lambda: httpx.put(f"{mock_ci}/repos/acme/web/pulls/{pr}/merge", headers={"Authorization": "Bearer t"}).status_code  # noqa: E731
    assert main(["baseline", "-c", str(cp)]) == 0
    page.write_text(HTML % {"color": "#dc2626"})
    assert main(["test", "-c", str(cp), "--ci", "auto"]) == 1
    st = httpx.get(f"{mock_ci}/__state").json()
    assert [s["state"] for s in st["statuses"]] == ["pending", "failure"]
    assert len(st["check_runs"]) == 1 and len(st["comments"][f"acme/web#{pr}"]) == 1
    assert merge() == 405  # visual regression blocks the merge
    assert main(["approve", "-c", str(cp)]) == 0
    assert main(["test", "-c", str(cp), "--ci", "auto"]) == 0
    st = httpx.get(f"{mock_ci}/__state").json()
    assert st["statuses"][-1]["state"] == "success" and len(st["comments"][f"acme/web#{pr}"]) == 1  # comment updated, not duplicated
    assert merge() == 200
