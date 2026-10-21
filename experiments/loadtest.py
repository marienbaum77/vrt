"""Performance measurements: comparison time / peak memory vs resolution, and the HTTP service under concurrency.

A.  in-process: the calibrated engine on a real baseline screenshot resized to each resolution, candidate = the
    same image with one visible regression (a block recoloured).  Median of 5 runs; peak memory by tracemalloc
    (Python + NumPy buffers) and the process RSS high-water mark.
B.  service: ``vrt serve`` (uvicorn) in a subprocess; POST /api/v1/compare with a 1280x720 pair at concurrency
    1, 2, 4, 8, 16; latency percentiles, throughput and server RSS (psutil).

    python -m experiments.loadtest
"""
from __future__ import annotations

import io
import json
import os
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List

import httpx
import numpy as np
import psutil
from PIL import Image

from diff_engine import compare_images
from metrics import measure_peak_memory, peak_rss_mb, percentile

from .common import DATA, RESULTS, calibrated_config

SIZES = [(640, 360), (1280, 720), (1920, 1080), (1920, 2160), (1920, 4320)]
CONCURRENCY = [1, 2, 4, 8, 16]
REQUESTS_PER_LEVEL = 48


def _source_image() -> Image.Image:
    base = sorted((DATA / "baselines").glob("*.png"))[0]
    return Image.open(base).convert("RGB")


def _pair(size, src: Image.Image):
    w, h = size
    # tile the screenshot vertically/horizontally so long pages keep realistic content statistics
    a = np.asarray(src)
    reps_y, reps_x = -(-h // a.shape[0]), -(-w // a.shape[1])
    big = np.tile(a, (reps_y, reps_x, 1))[:h, :w].copy()
    cand = big.copy()
    y0, x0 = h // 3, w // 4
    cand[y0:y0 + 40, x0:x0 + min(200, w // 3)] = (200, 40, 40)
    return big, cand


def _png(arr: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, "PNG")
    return buf.getvalue()


def resolution_benchmark(cfg) -> List[Dict[str, Any]]:
    src = _source_image()
    rows = []
    for size in SIZES:
        a, b = _pair(size, src)
        compare_images(a, b, cfg)  # warm-up
        times = []
        for _ in range(5):
            r = compare_images(a, b, cfg)
            times.append(r.diff_time_ms)
        peak = measure_peak_memory(lambda: compare_images(a, b, cfg))
        rows.append({"width": size[0], "height": size[1], "megapixels": round(size[0] * size[1] / 1e6, 2), "median_ms": float(np.median(times)), "min_ms": min(times),
                     "peak_traced_mb": round(peak, 1), "verdict": "failed" if not r.passed else "passed", "rss_highwater_mb": round(peak_rss_mb(), 1)})
        print(f"[perf] {size}: {np.median(times):.0f} ms, {peak:.0f} MB", flush=True)
    return rows


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def service_benchmark() -> Dict[str, Any]:
    port = _free_port()
    env = {**os.environ, "VRT_REPORTS_DIR": str(RESULTS / ".loadtest-reports")}
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "ci_adapter.server:app", "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
                            cwd=str(Path(__file__).resolve().parent.parent), env=env)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                if httpx.get(base + "/healthz", timeout=1).status_code == 200:
                    break
            except Exception:  # noqa: BLE001
                time.sleep(0.2)
        else:
            raise RuntimeError("server did not start")
        a, b = _pair((1280, 720), _source_image())
        pa, pb = _png(a), _png(b)
        ps = psutil.Process(proc.pid)
        levels = []
        with httpx.Client(base_url=base, timeout=120) as warm:
            warm.post("/api/v1/compare", files={"baseline": ("a.png", pa, "image/png"), "candidate": ("b.png", pb, "image/png")})
        for c in CONCURRENCY:
            lat: List[float] = []
            errors = 0

            def one(_: int) -> None:
                nonlocal errors
                t = time.perf_counter()
                try:
                    r = httpx.post(base + "/api/v1/compare", files={"baseline": ("a.png", pa, "image/png"), "candidate": ("b.png", pb, "image/png")}, timeout=120)
                    if r.status_code != 200:
                        errors += 1
                except Exception:  # noqa: BLE001
                    errors += 1
                lat.append((time.perf_counter() - t) * 1000)

            rss = [ps.memory_info().rss / 2**20]
            t0 = time.perf_counter()
            with ThreadPoolExecutor(c) as pool:
                futs = [pool.submit(one, i) for i in range(REQUESTS_PER_LEVEL)]
                while not all(f.done() for f in futs):
                    rss.append(ps.memory_info().rss / 2**20)
                    time.sleep(0.2)
            wall = time.perf_counter() - t0
            levels.append({"concurrency": c, "requests": REQUESTS_PER_LEVEL, "errors": errors, "rps": round(REQUESTS_PER_LEVEL / wall, 2),
                           "p50_ms": round(percentile(lat, 50), 1), "p95_ms": round(percentile(lat, 95), 1), "p99_ms": round(percentile(lat, 99), 1),
                           "server_rss_max_mb": round(max(rss), 1)})
            print(f"[load] c={c}: {levels[-1]}", flush=True)
        prom = httpx.get(base + "/metrics", timeout=10).text
        return {"payload": "1280x720 PNG pair, calibrated profile", "cpu_count": os.cpu_count(), "levels": levels, "metrics_endpoint_has_vrt_series": "vrt_diff_time_ms" in prom}
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()


def run() -> Dict[str, Any]:
    cal = json.loads((RESULTS / "calibration.json").read_text())
    cfg = calibrated_config(cal)
    out = {"resolution": resolution_benchmark(cfg), "service": service_benchmark(), "machine": {"cpus": os.cpu_count(), "python": sys.version.split()[0]}}
    (RESULTS / "performance.json").write_text(json.dumps(out, indent=2))
    L = ["# Performance", "", f"Machine: {out['machine']['cpus']} vCPU, Python {out['machine']['python']}.", "",
         "## Engine, calibrated config (screenshot tiled to size, one recoloured block)", "", "| resolution | MP | median ms | peak traced MB |", "|---|---|---|---|"]
    for r in out["resolution"]:
        L.append(f"| {r['width']}×{r['height']} | {r['megapixels']} | {r['median_ms']:.0f} | {r['peak_traced_mb']:.0f} |")
    L += ["", "## HTTP service, POST /api/v1/compare (1280×720)", "", "| concurrency | req/s | p50 ms | p95 ms | p99 ms | errors | server RSS max MB |", "|---|---|---|---|---|---|---|"]
    for l in out["service"]["levels"]:
        L.append(f"| {l['concurrency']} | {l['rps']} | {l['p50_ms']} | {l['p95_ms']} | {l['p99_ms']} | {l['errors']} | {l['server_rss_max_mb']} |")
    (RESULTS / "PERFORMANCE.md").write_text("\n".join(L) + "\n")
    print("\n".join(L))
    return out


if __name__ == "__main__":
    run()
