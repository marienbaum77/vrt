"""Capture-stabilisation ablation: how many false alarms does the capture module remove?

Five kinds of deliberately unstable pages are served by a local HTTP server and each is captured
several times *without any change to the page*.  Every capture after the first is compared with the
first one using the calibrated engine, so every flagged pair is a false positive.

Modes (cumulative):
  naive    no waiting, no frozen clock, animations running, one screenshot
  stable   fonts/images wait, animations frozen, Date/Math.random frozen, two-equal-frames loop
  masked   stable + CSS-selector masks for the one element that is genuinely dynamic
  auto     stable + masks found automatically from repeated captures (discover_volatile_regions)

A negative control checks that masking does not hide real regressions outside the masked area.

    python -m experiments.stability
"""
from __future__ import annotations

import itertools
import json
import random
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

import numpy as np
from PIL import Image

from capture import CaptureSession, CaptureSpec, discover_volatile_regions
from capture.stabilize import Stabilize
from diff_engine import DiffConfig, compare_images
from metrics import stats

from .common import RESULTS, calibrated_config, pct

REPEATS = 5
VARIANTS = 4
VIEWPORT = (900, 560)

PAGE_KINDS = ["animation", "delayed_content", "slow_image", "clock", "live_value"]
MASK_FOR = {"live_value": ["#live"]}  # only the server-driven value cannot be made deterministic by the capture module

NAIVE = Stabilize(wait_fonts=False, wait_images=False, network_idle=False, disable_animations=False, hide_caret=False, freeze_time=None, max_attempts=1)
STABLE = Stabilize()
STABLE_REAL_TIME = Stabilize(freeze_time=None)

_COUNTER = itertools.count(1000)
_LOCK = threading.Lock()


def _png(seed: int) -> bytes:
    rng = np.random.default_rng(seed)
    x = np.linspace(0, 1, 360)[None, :, None]
    y = np.linspace(0, 1, 140)[:, None, None]
    base = rng.uniform(40, 200, 3)[None, None, :]
    img = np.clip(base + 55 * np.sin(6 * x + seed) * np.cos(4 * y), 0, 255).astype(np.uint8)
    buf = BytesIO()
    Image.fromarray(np.broadcast_to(img, (140, 360, 3)).copy()).save(buf, "PNG")
    return buf.getvalue()


def _shell(body: str, script: str, v: int, regress: bool) -> str:
    rng = random.Random(v)
    hue = rng.choice([210, 150, 28, 280])
    head = "#b00020" if regress else f"hsl({hue} 45% 22%)"
    btn_margin = 14 if regress else 8
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
body{{margin:0;font:16px/1.5 system-ui,sans-serif;background:#fafafa;color:#222}}
header{{padding:18px 28px;background:#fff;border-bottom:1px solid #ddd}}
h1{{margin:0;font-size:24px;color:{head}}}
main{{padding:24px 28px;max-width:820px}}
.card{{background:#fff;border:1px solid #e3e3e3;border-radius:8px;padding:14px 18px;margin:0 0 14px}}
.btn{{display:inline-block;padding:8px 16px;border-radius:6px;background:hsl({hue} 60% 40%);color:#fff;margin-left:{btn_margin}px}}
</style></head><body><header><h1>Dashboard variant {v}</h1></header><main>
<div class="card"><p>Quarterly summary for team {v}. Static text that never changes between captures, used to make the page realistic.</p><span class="btn">Open report</span></div>
{body}
<div class="card"><p>Footer note: all figures are provisional.</p></div></main><script>{script}</script></body></html>"""


def page_html(kind: str, v: int, regress: bool = False) -> str:
    if kind == "animation":
        body = ("<div class='card'><div id='sp' style='width:60px;height:60px;border-radius:50%;border:6px solid #ccc;border-top-color:#07c;"
                "animation:spin 1s linear infinite'></div><div id='bar' style='height:10px;margin-top:12px;width:60%;"
                "background:linear-gradient(90deg,#07c,#9cf,#07c);background-size:200% 100%;animation:slide 1.3s linear infinite'></div></div>"
                "<style>@keyframes spin{to{transform:rotate(360deg)}}@keyframes slide{to{background-position:200% 0}}</style>")
        return _shell(body, "", v, regress)
    if kind == "delayed_content":
        body = "<div id='late'></div>"
        script = "setTimeout(()=>{document.getElementById('late').innerHTML=\"<div class='card'><p>Recent activity loaded asynchronously.</p><p>Three new events.</p></div>\"}," + str(random.Random().randint(120, 320)) + ");"
        return _shell(body, script, v, regress)
    if kind == "slow_image":
        body = "<div id='slot'></div>"
        script = f"setTimeout(()=>{{var i=new Image();i.src='/img.png?s={v}&d='+(100+Math.floor(Math.random()*350));document.getElementById('slot').appendChild(i);}},40);"
        return _shell(body, script, v, regress)
    if kind == "clock":
        body = "<div class='card'>Last refreshed at <b id='t'></b> &mdash; tick <b id='n'></b></div>"
        script = ("var t0=Date.now();function f(){document.getElementById('t').textContent=new Date().toLocaleTimeString('en-US',{timeZone:'UTC'});"
                  "document.getElementById('n').textContent=Math.floor((Date.now()-t0)/50)+Math.floor(Math.random()*7);}f();setInterval(f,50);")
        return _shell(body, script, v, regress)
    if kind == "live_value":
        body = "<div class='card'>Active sessions: <b id='live'>&hellip;</b></div>"
        script = "fetch('/api/counter').then(r=>r.text()).then(t=>{document.getElementById('live').textContent=t});"
        return _shell(body, script, v, regress)
    raise ValueError(kind)


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a: Any) -> None:  # silence
        pass

    def do_GET(self) -> None:  # noqa: N802
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path.startswith("/page/"):
            _, _, kind, v = u.path.split("/")
            data = page_html(kind, int(v), regress=q.get("regress") == ["1"]).encode()
            ctype = "text/html; charset=utf-8"
        elif u.path == "/img.png":
            time.sleep(int(q.get("d", ["0"])[0]) / 1000.0)
            data, ctype = _png(int(q.get("s", ["0"])[0])), "image/png"
        elif u.path == "/api/counter":
            with _LOCK:
                data, ctype = str(next(_COUNTER)).encode(), "text/plain"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):  # the browser abandoned a slow request
            pass


def _spec(url: str, stab: Stabilize, masks: Optional[List[str]] = None, regions: Optional[List[Dict[str, float]]] = None) -> CaptureSpec:
    return CaptureSpec(name="p", url=url, viewport_width=VIEWPORT[0], viewport_height=VIEWPORT[1], stabilize=stab,
                       mask_selectors=masks or [], mask_regions=regions or [])


def _cmp(a, b, cfg: DiffConfig) -> bool:
    """True if the engine flags the pair as changed."""
    r = compare_images(a.png, b.png, cfg, (a.regions + b.regions) or None)
    return not r.passed


def run(repeats: int = REPEATS, variants: int = VARIANTS) -> Dict[str, Any]:
    cal = json.loads((RESULTS / "calibration.json").read_text())
    cfg = calibrated_config(cal)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    t0 = time.time()
    out: Dict[str, Any] = {"repeats": repeats, "variants": variants, "modes": {}, "regression_control": {}}
    flags: Dict[Tuple[str, str], List[int]] = {}
    with CaptureSession() as sess:
        for kind in PAGE_KINDS:
            for v in range(variants):
                url = f"{base}/page/{kind}/{v}"
                # volatile regions discovered from three real-time captures
                frames = [sess.capture(_spec(url, STABLE_REAL_TIME)).png for _ in range(3)]
                auto = discover_volatile_regions(frames)
                modes = {
                    "naive": _spec(url, NAIVE),
                    "stable": _spec(url, STABLE),
                    "masked": _spec(url, STABLE, masks=MASK_FOR.get(kind)),
                    "auto": _spec(url, STABLE, regions=auto),
                }
                for mode, spec in modes.items():
                    shots = [sess.capture(spec) for _ in range(repeats + 1)]
                    flags.setdefault((kind, mode), []).extend(int(_cmp(shots[0], s, cfg)) for s in shots[1:])
                print(f"[stability] {kind} v{v} done ({time.time() - t0:.0f}s)", flush=True)

        # negative control: a real regression (heading colour + button moved 6 px) on every kind, with masks active
        rc: Dict[str, List[int]] = {}
        for kind in PAGE_KINDS:
            for v in range(variants):
                for mode, mk in (("masked", MASK_FOR.get(kind)), ("stable", None)):
                    a = sess.capture(_spec(f"{base}/page/{kind}/{v}", STABLE, masks=mk))
                    b = sess.capture(_spec(f"{base}/page/{kind}/{v}?regress=1", STABLE, masks=mk))
                    rc.setdefault(mode, []).append(int(_cmp(a, b, cfg)))
        out["regression_control"] = {m: {"n": len(x), "detected": int(sum(x)), "recall": sum(x) / len(x), "ci": stats.wilson_interval(int(sum(x)), len(x))} for m, x in rc.items()}
    srv.shutdown()

    for mode in ("naive", "stable", "masked", "auto"):
        per = {}
        tot_n = tot_fp = 0
        for kind in PAGE_KINDS:
            f = flags[(kind, mode)]
            per[kind] = {"n": len(f), "false_positives": int(sum(f)), "fp_rate": sum(f) / len(f), "ci": stats.wilson_interval(int(sum(f)), len(f))}
            tot_n += len(f)
            tot_fp += sum(f)
        out["modes"][mode] = {"per_kind": per, "n": tot_n, "false_positives": int(tot_fp), "fp_rate": tot_fp / tot_n, "ci": stats.wilson_interval(int(tot_fp), tot_n)}
    out["runtime_s"] = round(time.time() - t0, 1)
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "stability.json").write_text(json.dumps(out, indent=2, default=lambda o: o.item() if hasattr(o, "item") else list(o)))

    L = ["# Capture-stabilisation ablation", "",
         f"{len(PAGE_KINDS)} unstable page kinds × {variants} variants × {repeats} repeat captures; every pair is *unchanged* by construction, so each flagged pair is a false positive.", "",
         "| page kind | " + " | ".join(out["modes"]) + " |", "|---|" + "---|" * len(out["modes"])]
    for kind in PAGE_KINDS:
        L.append(f"| {kind} | " + " | ".join(f"{m['per_kind'][kind]['false_positives']}/{m['per_kind'][kind]['n']}" for m in out["modes"].values()) + " |")
    L.append("| **all** | " + " | ".join(f"**{pct(m['fp_rate'])}** ({m['false_positives']}/{m['n']})" for m in out["modes"].values()) + " |")
    L += ["", "Regression control (real change outside the masked element): " + "; ".join(f"{k}: {v['detected']}/{v['n']} detected" for k, v in out["regression_control"].items()), ""]
    (RESULTS / "STABILITY.md").write_text("\n".join(L))
    print("\n".join(L))
    return out


if __name__ == "__main__":
    run()
