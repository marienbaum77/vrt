"""Ground-truth mutations (real UI regressions) and rendering-noise variants.

*Positive* (label = 1, "changed") pairs apply one **mutation** whose magnitude is above the
visibility floors declared in :data:`FLOORS` (decided a-priori, see docs/DATASET.md).
*Negative* (label = 0, "unchanged") pairs apply only **noise**: environment-level
differences that a human reviewer would never call a regression.
"""
from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

from diff_engine.color import delta_e_2000, srgb_to_lab

from .scenes import WORDS

# a-priori visibility floors (what counts as a regression a reviewer must see)
FLOORS = {
    "text_digit": "1 glyph changed",
    "text_word": "1 word replaced",
    "color_bg": "dE2000 >= 5 on a >= 400 px^2 surface",
    "color_text": "dE2000 >= 10 on text",
    "shift": ">= 2 px displacement",
    "resize": ">= 3 px size change",
    "remove": "element >= 150 px^2 hidden",
    "add": "16x16 px badge added",
    "font": "font-size +1px or weight flip",
    "radius": "border-radius +8px on a >= 3000 px^2 surface",
    "opacity": "opacity <= 0.85",
}

POSITIVE_WEIGHTS = {
    "text_digit": 12,
    "text_word": 12,
    "color_bg": 14,
    "color_text": 8,
    "shift": 14,
    "resize": 10,
    "remove": 8,
    "add": 6,
    "font": 8,
    "radius": 4,
    "opacity": 4,
}

NEGATIVE_WEIGHTS = {
    "identical": 8,
    "subpixel_text": 16,
    "kerning": 8,
    "letter_spacing": 8,
    "element_translate": 16,
    "page_translate": 6,
    "color_drift": 8,
    "composited": 6,
    "masked_dynamic": 16,
    "combined": 8,
}

# noise that may be layered on top of a real mutation (realistic: regressions happen on noisy machines)
OVERLAY_NOISE = ["subpixel_text", "kerning", "letter_spacing", "color_drift", "composited", "masked_dynamic"]

PICK_FN = """
function vrtPick(token, u, minArea, maxArea) {
  const els = Array.from(document.querySelectorAll('[data-m~="' + token + '"]')).filter(e => {
    const r = e.getBoundingClientRect(), cs = getComputedStyle(e), a = r.width * r.height;
    return a >= minArea && a <= maxArea && r.left >= 0 && r.top >= 0 && r.right <= innerWidth && r.bottom <= innerHeight
      && cs.visibility !== 'hidden' && cs.display !== 'none' && !e.closest('[data-vrt-ignore]');
  });
  if (!els.length) return null;
  return els[Math.floor(u * els.length) % els.length];
}
function vrtInfo(e) {
  const r = e.getBoundingClientRect(), cs = getComputedStyle(e);
  return { x: r.x, y: r.y, w: r.width, h: r.height, bg: cs.backgroundColor, color: cs.color, fs: parseFloat(cs.fontSize),
           fw: cs.fontWeight, text: e.textContent, leaf: e.children.length === 0, radius: parseFloat(cs.borderTopLeftRadius) || 0,
           position: cs.position, display: cs.display };
}
"""

PROBE_JS = "(a) => {" + PICK_FN + " const e = vrtPick(a[0], a[1], a[2], a[3]); return e ? vrtInfo(e) : null; }"


@dataclass
class MutationPlan:
    category: str
    magnitude: float
    unit: str
    detail: str
    js: str


@dataclass
class NoisePlan:
    names: List[str]
    css: str = ""
    js: str = ""
    variant: str = "default"  # which browser instance renders the candidate


class NoTarget(Exception):
    """No suitable element for this mutation in this scene."""


def _apply_js(token: str, u: float, min_area: float, max_area: float, body: str) -> str:
    return (
        "(() => {" + PICK_FN + f"const e = vrtPick({json.dumps(token)}, {u}, {min_area}, {max_area});"
        "if (!e) { throw new Error('mutation target not found'); }" + body + "})();"
    )


_RGB = re.compile(r"rgba?\(\s*(\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?\s*\)")


def parse_rgb(css: str) -> Tuple[Tuple[int, int, int], float]:
    m = _RGB.match(css)
    if not m:
        raise NoTarget(f"cannot parse colour {css!r}")
    a = float(m.group(4)) if m.group(4) is not None else 1.0
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))), a


def recolor(rgb: Tuple[int, int, int], target_de: float, rng: random.Random) -> Tuple[Tuple[int, int, int], float]:
    """Return a colour whose CIEDE2000 distance from ``rgb`` is within +-12 % of ``target_de``."""
    lab0 = srgb_to_lab(np.array(rgb, dtype=np.uint8))
    base = np.array(rgb, dtype=np.float64)
    for _ in range(60):
        d = np.array([rng.gauss(0, 1) for _ in range(3)])
        d /= np.linalg.norm(d)
        lo, hi = 0.0, 300.0
        for _ in range(28):
            mid = (lo + hi) / 2
            cand = np.clip(base + d * mid, 0, 255).round().astype(np.uint8)
            de = float(delta_e_2000(lab0, srgb_to_lab(cand)))
            lo, hi = (mid, hi) if de < target_de else (lo, mid)
        cand = np.clip(base + d * hi, 0, 255).round().astype(np.uint8)
        de = float(delta_e_2000(lab0, srgb_to_lab(cand)))
        if abs(de - target_de) <= 0.12 * target_de:
            return (int(cand[0]), int(cand[1]), int(cand[2])), de
    raise NoTarget("could not hit target dE")


Probe = Callable[[str, float, float, float], Optional[dict]]


def plan_mutation(kind: str, probe: Probe, rng: random.Random, force: Optional[float] = None) -> MutationPlan:
    """Plan one mutation.  ``force`` pins the magnitude (px for shift/resize, dE2000 for colours);
    it is only used by the sensitivity sweep, never by the main dataset."""
    u = rng.random()
    if kind == "text_digit":
        info = probe("num", u, 60, 60000)
        if not info or not info["leaf"]:
            raise NoTarget(kind)
        text = info["text"]
        pos = [i for i, ch in enumerate(text) if ch.isdigit()]
        if not pos:
            raise NoTarget(kind)
        i = rng.choice(pos)
        new_digit = rng.choice([d for d in "0123456789" if d != text[i]])
        new = text[:i] + new_digit + text[i + 1 :]
        js = _apply_js("num", u, 60, 60000, f"e.textContent = {json.dumps(new)};")
        return MutationPlan(kind, 1, "glyph", f"{text!r} -> {new!r}", js)
    if kind == "text_word":
        info = probe("text", u, 80, 20000)
        if not info or not info["leaf"]:
            raise NoTarget(kind)
        words = info["text"].split(" ")
        cand = [i for i, w in enumerate(words) if len(w) >= 4 and w.isalpha()]
        if not cand:
            raise NoTarget(kind)
        i = rng.choice(cand)
        new_w = rng.choice([w for w in WORDS if w != words[i].lower() and abs(len(w) - len(words[i])) <= 3])
        new_w = new_w.capitalize() if words[i][0].isupper() else new_w
        new = " ".join(words[:i] + [new_w] + words[i + 1 :])
        js = _apply_js("text", u, 80, 20000, f"e.textContent = {json.dumps(new)};")
        return MutationPlan(kind, 1, "word", f"{words[i]!r} -> {new_w!r}", js)
    if kind == "color_bg":
        info = probe("block", u, 400, 400000)
        if not info:
            raise NoTarget(kind)
        rgb, alpha = parse_rgb(info["bg"])
        if alpha < 0.9:
            raise NoTarget("transparent background")
        target = force if force is not None else rng.uniform(5, 40)
        new, de = recolor(rgb, target, rng)
        js = _apply_js("block", u, 400, 400000, f"e.style.backgroundColor = 'rgb({new[0]},{new[1]},{new[2]})'; e.style.backgroundImage = 'none';")
        return MutationPlan(kind, round(de, 2), "dE2000", f"background {rgb} -> {new}", js)
    if kind == "color_text":
        info = probe("text", u, 80, 20000)
        if not info or not info["leaf"] or len(info["text"].strip()) < 3:
            raise NoTarget(kind)
        rgb, _ = parse_rgb(info["color"])
        target = force if force is not None else rng.uniform(10, 50)
        new, de = recolor(rgb, target, rng)
        js = _apply_js("text", u, 80, 20000, f"e.style.color = 'rgb({new[0]},{new[1]},{new[2]})';")
        return MutationPlan(kind, round(de, 2), "dE2000", f"text colour {rgb} -> {new}", js)
    if kind == "shift":
        tok = rng.choice(["text", "block", "icon"])
        info = probe(tok, u, 150, 60000)
        if not info:
            raise NoTarget(kind)
        d = (force if force is not None else rng.choice([2, 3, 4, 5, 6, 8])) * rng.choice([-1, 1])
        d = int(d)
        axis = rng.choice(["left", "top"])
        js = _apply_js(tok, u, 150, 60000, f"e.style.position = e.style.position || 'relative'; if (getComputedStyle(e).position === 'static') {{ e.style.position = 'relative'; }} e.style.{axis} = '{d}px';")
        return MutationPlan(kind, abs(d), "px", f"{tok} moved {d}px ({axis})", js)
    if kind == "resize":
        info = probe("block", u, 600, 400000)
        if not info:
            raise NoTarget(kind)
        dim = rng.choice(["width", "height"])
        delta = rng.choice([-1, 1]) * (int(force) if force is not None else rng.randint(3, 24))
        cur = info["w"] if dim == "width" else info["h"]
        if cur + delta < 12:
            delta = abs(delta)
        js = _apply_js("block", u, 600, 400000, f"e.style.boxSizing = 'border-box'; e.style.flex = 'none'; e.style.{dim} = '{round(cur + delta, 2)}px';")
        return MutationPlan(kind, abs(delta), "px", f"block {dim} {delta:+d}px", js)
    if kind == "remove":
        tok = rng.choice(["icon", "btn"])
        info = probe(tok, u, 150, 20000)
        if not info:
            raise NoTarget(kind)
        js = _apply_js(tok, u, 150, 20000, "e.style.visibility = 'hidden';")
        return MutationPlan(kind, round(info["w"] * info["h"]), "px^2", f"{tok} hidden", js)
    if kind == "add":
        info = probe("block", u, 3000, 400000)
        if not info:
            raise NoTarget(kind)
        corner = rng.choice(["top:6px;right:6px", "top:6px;left:6px", "bottom:6px;right:6px"])
        color = rng.choice(["#ef4444", "#f59e0b", "#22c55e", "#8b5cf6"])
        js = _apply_js(
            "block", u, 3000, 400000,
            "if (getComputedStyle(e).position === 'static') { e.style.position = 'relative'; }"
            "const b = document.createElement('span');"
            f"b.style.cssText = 'position:absolute;{corner};width:16px;height:16px;border-radius:8px;background:{color}';"
            "e.appendChild(b);",
        )
        return MutationPlan(kind, 256, "px^2", "16x16 badge added", js)
    if kind == "font":
        info = probe("text", u, 80, 20000)
        if not info or not info["leaf"] or len(info["text"].strip()) < 4:
            raise NoTarget(kind)
        mode = rng.choice(["size+1", "size+2", "weight"])
        if mode == "weight":
            new_w = "700" if int(info["fw"]) < 600 else "400"
            body, detail = f"e.style.fontWeight = '{new_w}';", f"font-weight {info['fw']} -> {new_w}"
        else:
            inc = 1 if mode == "size+1" else 2
            body, detail = f"e.style.fontSize = '{info['fs'] + inc}px';", f"font-size {info['fs']} -> {info['fs'] + inc}px"
        js = _apply_js("text", u, 80, 20000, body)
        return MutationPlan(kind, 1, "font step", detail, js)
    if kind == "radius":
        info = probe("block", u, 3000, 400000)
        if not info:
            raise NoTarget(kind)
        inc = rng.choice([8, 12, 16])
        js = _apply_js("block", u, 3000, 400000, f"e.style.borderRadius = '{info['radius'] + inc}px'; e.style.overflow = 'hidden';")
        return MutationPlan(kind, inc, "px", f"border-radius {info['radius']:.0f} -> {info['radius'] + inc:.0f}px", js)
    if kind == "opacity":
        info = probe("block", u, 1500, 400000)
        if not info:
            raise NoTarget(kind)
        v = round(rng.uniform(0.45, 0.85), 2)
        js = _apply_js("block", u, 1500, 400000, f"e.style.opacity = '{v}';")
        return MutationPlan(kind, v, "opacity", f"opacity 1 -> {v}", js)
    raise ValueError(kind)


# ----------------------------------------------------------------------- noise
_DYNAMIC_JS = """
(() => {
  const els = document.querySelectorAll('[data-vrt-ignore]');
  if (!els.length) { throw new Error('no dynamic element'); }
  els.forEach(e => {
    if (e.classList.contains('clock')) { e.textContent = '%(clock)s'; }
    else if (e.classList.contains('live')) { e.textContent = '%(live)s online'; }
    else if (e.classList.contains('ad')) {
      e.style.background = 'linear-gradient(90deg,%(c1)s,%(c2)s)';
      const s = e.querySelector('span'); if (s) { s.textContent = 'Sponsored: %(ad)s'; }
      const b = e.querySelector('b'); if (b) { b.textContent = '%(pct)d%% off'; }
    }
  });
})();
"""


def _translate_blocks_js(rng: random.Random) -> str:
    n = rng.randint(1, 3)
    parts = []
    for _ in range(n):
        u = rng.random()
        fx = rng.choice([-1, 1]) * rng.uniform(0.2, 0.5)
        fy = rng.choice([-1, 1]) * rng.uniform(0.2, 0.5)
        parts.append(f"(() => {{{PICK_FN} const e = vrtPick('block', {u}, 2000, 400000); if (e) {{ e.style.transform = 'translate({fx:.2f}px,{fy:.2f}px)'; }} }})();")
    return "".join(parts)


def _composited_js(rng: random.Random) -> str:
    parts = []
    for _ in range(rng.randint(2, 4)):
        u = rng.random()
        parts.append(f"(() => {{{PICK_FN} const e = vrtPick('block', {u}, 1500, 400000); if (e) {{ e.style.willChange = 'transform'; }} }})();")
    return "".join(parts)


def plan_noise(kind: str, rng: random.Random, has_dynamic: bool = True) -> NoisePlan:
    if kind == "identical":
        return NoisePlan(["identical"])
    if kind == "subpixel_text":
        return NoisePlan(["subpixel_text"], variant="nosub")
    if kind == "kerning":
        return NoisePlan(["kerning"], css="html{font-kerning:none !important}")
    if kind == "letter_spacing":
        return NoisePlan(["letter_spacing"], css=f"html{{letter-spacing:{rng.choice([0.003, 0.004, 0.006])}px !important}}")
    if kind == "element_translate":
        return NoisePlan(["element_translate"], js=_translate_blocks_js(rng))
    if kind == "page_translate":
        f = rng.uniform(0.2, 0.4)
        return NoisePlan(["page_translate"], js=f"document.body.style.transform = 'translate({f:.2f}px,{f * 0.7:.2f}px)';")
    if kind == "color_drift":
        return NoisePlan(["color_drift"], js=f"document.body.style.opacity = '{rng.uniform(0.985, 0.998):.4f}';")
    if kind == "composited":
        return NoisePlan(["composited"], js=_composited_js(rng))
    if kind == "masked_dynamic":
        if not has_dynamic:
            raise NoTarget("scene has no dynamic regions")
        js = _DYNAMIC_JS % {
            "clock": f"{rng.randint(0,23):02d}:{rng.randint(0,59):02d}:{rng.randint(0,59):02d}",
            "live": f"{rng.randint(100, 9999):,}",
            "c1": rng.choice(["#f59e0b", "#06b6d4", "#ec4899"]),
            "c2": rng.choice(["#ef4444", "#8b5cf6", "#10b981"]),
            "ad": " ".join(rng.sample(WORDS, 2)).capitalize() + " - new arrivals",
            "pct": rng.randint(10, 60),
        }
        return NoisePlan(["masked_dynamic"], js=js)
    if kind == "combined":
        pool = ["subpixel_text", "kerning", "letter_spacing", "element_translate", "color_drift", "composited"]
        a, b = rng.sample(pool, 2)
        pa, pb = plan_noise(a, rng, has_dynamic), plan_noise(b, rng, has_dynamic)
        return merge_noise([pa, pb], names=["combined:" + a, "combined:" + b])
    raise ValueError(kind)


def merge_noise(plans: List[NoisePlan], names: Optional[List[str]] = None) -> NoisePlan:
    variant = "nosub" if any(p.variant == "nosub" for p in plans) else "default"
    return NoisePlan(
        names=names or [n for p in plans for n in p.names],
        css="".join(p.css for p in plans),
        js="".join(p.js for p in plans),
        variant=variant,
    )
