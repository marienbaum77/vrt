"""Report generation: overlay diff, side-by-side comparison, JSON metrics, HTML index."""
from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .engine import DiffResult

# colours
DIFF_RGB = (255, 0, 60)
TOLERATED_RGB = (255, 196, 0)
IGNORE_RGB = (60, 120, 255)


def _font(size: int = 14):
    for name in ("DejaVuSans.ttf", "Inter-Regular.ttf", "FreeSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def make_overlay(res: DiffResult, dim: float = 0.35) -> Image.Image:
    """Candidate dimmed to grey with: red = real diff, amber = tolerated, blue = ignored zone."""
    art = res.artifacts
    cand = art["candidate"].astype(np.float32)
    grey = cand.mean(axis=2, keepdims=True)
    base = (255 * (1 - dim) + grey * dim).repeat(3, axis=2)
    out = base.copy()
    for key, rgb, alpha in (("ignore_mask", IGNORE_RGB, 0.25), ("tolerated_mask", TOLERATED_RGB, 0.9), ("diff_mask", DIFF_RGB, 1.0)):
        m = art[key]
        if m.any():
            out[m] = out[m] * (1 - alpha) + np.array(rgb, dtype=np.float32) * alpha
    img = Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))
    d = ImageDraw.Draw(img)
    for r in res.regions:
        d.rectangle([r["x"] - 3, r["y"] - 3, r["x"] + r["w"] + 3, r["y"] + r["h"] + 3], outline=DIFF_RGB, width=2)
    return img


def make_side_by_side(res: DiffResult, overlay: Optional[Image.Image] = None, label: str = "") -> Image.Image:
    art = res.artifacts
    panels = [
        ("baseline", Image.fromarray(art["baseline"])),
        ("candidate", Image.fromarray(art["candidate"])),
        ("diff", overlay or make_overlay(res)),
    ]
    w, h = panels[0][1].size
    gap, head = 12, 28
    sheet = Image.new("RGB", (w * 3 + gap * 4, h + head + gap * 2), (245, 246, 248))
    d = ImageDraw.Draw(sheet)
    f = _font(15)
    for i, (title, im) in enumerate(panels):
        x = gap + i * (w + gap)
        d.text((x, gap), f"{title}" + (f" - {label}" if label and i == 2 else ""), fill=(40, 44, 52), font=f)
        sheet.paste(im, (x, gap + head))
        d.rectangle([x - 1, gap + head - 1, x + w, gap + head + h], outline=(200, 204, 212))
    return sheet


def write_page_artifacts(
    out_dir: Path, name: str, res: DiffResult, extra: Optional[Dict[str, Any]] = None
) -> Dict[str, str]:
    """Write baseline/candidate/overlay/side-by-side PNGs and the JSON metrics of one page."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    files: Dict[str, str] = {}
    if res.artifacts:
        overlay = make_overlay(res)
        sbs = make_side_by_side(res, overlay, label=f"{res.diff_pixels} px")
        for key, im in (
            ("baseline", Image.fromarray(res.artifacts["baseline"])),
            ("candidate", Image.fromarray(res.artifacts["candidate"])),
            ("overlay", overlay),
            ("side_by_side", sbs),
        ):
            fn = f"{name}.{key}.png"
            im.save(out_dir / fn, optimize=False, compress_level=3)
            files[key] = fn
    payload = res.to_dict()
    payload["page"] = name
    if extra:
        payload.update(extra)
    payload["files"] = files
    (out_dir / f"{name}.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    files["json"] = f"{name}.json"
    return files


_CSS = """
:root{--bg:#f5f6f8;--fg:#1f2430;--card:#fff;--ok:#12805c;--bad:#c62828;--mut:#6b7280;--line:#e3e6ec}
@media (prefers-color-scheme:dark){:root{--bg:#14161b;--fg:#e8eaf0;--card:#1d2027;--ok:#4cc38a;--bad:#ff6b6b;--mut:#9aa3b2;--line:#2c313b}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 Inter,system-ui,sans-serif}
main{max-width:1280px;margin:0 auto;padding:24px 16px 64px}h1{font-size:22px;margin:0 0 4px}
.sub{color:var(--mut);margin-bottom:20px}.sum{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:24px}
.tile{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 16px;min-width:120px}
.tile b{display:block;font-size:22px}.tile span{color:var(--mut);font-size:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;margin-bottom:20px}
.card h2{font-size:16px;margin:0 0 8px;display:flex;gap:10px;align-items:center}
.badge{font-size:12px;padding:2px 8px;border-radius:99px;font-weight:600}
.pass{background:color-mix(in srgb,var(--ok) 18%,transparent);color:var(--ok)}
.fail{background:color-mix(in srgb,var(--bad) 18%,transparent);color:var(--bad)}
.new{background:color-mix(in srgb,#b7791f 22%,transparent);color:#b7791f}
table{border-collapse:collapse;width:100%;font-size:13px;margin:8px 0}td,th{padding:4px 8px;border-bottom:1px solid var(--line);text-align:left}
img{max-width:100%;border:1px solid var(--line);border-radius:6px}.mut{color:var(--mut)}
.cmp{position:relative;display:inline-block;max-width:100%;line-height:0}.cmp img.top{position:absolute;left:0;top:0;clip-path:inset(0 50% 0 0)}
input[type=range]{width:100%}details summary{cursor:pointer;margin:6px 0;color:var(--mut)}
"""

_JS = """
function sl(id,v){document.getElementById(id).style.clipPath='inset(0 '+(100-v)+'% 0 0)'}
"""


def write_html_report(
    out_dir: Path,
    page_results: List[Dict[str, Any]],
    summary: Dict[str, Any],
    title: str = "Visual regression report",
) -> Path:
    """Self-contained HTML index (images referenced relatively) for a whole suite run."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    e = html.escape
    parts: List[str] = [
        f"<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>{e(title)}</title><style>{_CSS}</style><main>",
        f"<h1>{e(title)}</h1><div class=sub>{e(str(summary.get('generated_at', '')))} &middot; config {e(str(summary.get('config', '')))}</div>",
        "<div class=sum>",
    ]
    for label, key in (("pages", "total"), ("passed", "passed"), ("failed", "failed"), ("new / no baseline", "missing_baseline"), ("errors", "errors")):
        parts.append(f"<div class=tile><b>{summary.get(key, 0)}</b><span>{label}</span></div>")
    parts.append(f"<div class=tile><b>{summary.get('verdict', '')}</b><span>verdict</span></div></div>")

    for r in page_results:
        status = r["status"]
        cls = {"passed": "pass", "new": "new"}.get(status, "fail")
        parts.append(f"<section class=card><h2>{e(r['page'])} <span class='badge {cls}'>{e(status)}</span></h2>")
        if status == "new":
            cand = r.get("files", {}).get("candidate")
            parts.append("<p class=mut>No baseline stored for this page. Review the capture, then run <code>vrt approve</code>.</p>")
            if cand:
                parts.append(f"<a href='{e(cand)}'><img loading=lazy src='{e(cand)}' alt='candidate'></a>")
            parts.append("</section>")
            continue
        if r.get("error"):
            parts.append(f"<p class=mut>{e(r['error'])}</p></section>")
            continue
        parts.append(
            "<table><tr><th>significant px</th><th>ratio</th><th>raw px</th><th>max &Delta;E</th><th>regions</th><th>time</th></tr>"
            f"<tr><td>{r['diff_pixels']}</td><td>{r['diff_ratio']:.6f}</td><td>{r['raw_diff_pixels']}</td>"
            f"<td>{r['max_delta_e']:.2f}</td><td>{len(r['regions'])}</td><td>{r['diff_time_ms']:.0f} ms</td></tr></table>"
        )
        if r.get("reasons"):
            parts.append("<p class=mut>" + e("; ".join(r["reasons"])) + "</p>")
        f = r.get("files", {})
        if "side_by_side" in f:
            parts.append(f"<a href='{e(f['side_by_side'])}'><img loading=lazy src='{e(f['side_by_side'])}' alt='side by side'></a>")
            sid = "s_" + "".join(c if c.isalnum() else "_" for c in r["page"])
            parts.append(
                f"<details><summary>swipe: baseline | candidate</summary><div class=cmp><img src='{e(f['baseline'])}'>"
                f"<img id='{sid}' class=top src='{e(f['candidate'])}'></div>"
                f"<input type=range min=0 max=100 value=50 oninput=\"sl('{sid}',this.value)\"></details>"
            )
        if r.get("regions"):
            rows = "".join(
                f"<tr><td>{g['x']},{g['y']}</td><td>{g['w']}&times;{g['h']}</td><td>{g['area']}</td><td>{g['max_delta_e']}</td></tr>"
                for g in r["regions"][:15]
            )
            parts.append(f"<details><summary>top regions</summary><table><tr><th>x,y</th><th>size</th><th>px</th><th>max &Delta;E</th></tr>{rows}</table></details>")
        parts.append("</section>")
    parts.append(f"</main><script>{_JS}</script></html>")
    path = out_dir / "index.html"
    path.write_text("".join(parts), encoding="utf-8")
    return path
