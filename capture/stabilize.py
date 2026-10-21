"""Render-stabilisation primitives (JS snippets + settings) used by the capture session."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

FREEZE_TIME_JS = """
(() => {
  const fixed = %(ts)d;
  const RealDate = Date;
  class FrozenDate extends RealDate {
    constructor(...args) { if (args.length === 0) { super(fixed); } else { super(...args); } }
    static now() { return fixed; }
  }
  window.Date = FrozenDate;
  let seed = 1234567;
  Math.random = function () { seed = (seed * 1664525 + 1013904223) %% 4294967296; return seed / 4294967296; };
})();
"""

# animations/transitions off, caret hidden, no smooth scrolling, stable scrollbars
STABILISE_CSS = """
*, *::before, *::after {
  animation-delay: 0s !important; animation-duration: 0s !important; animation-iteration-count: 1 !important;
  transition-delay: 0s !important; transition-duration: 0s !important; scroll-behavior: auto !important;
  caret-color: transparent !important;
}
html { scrollbar-width: none !important; }
::-webkit-scrollbar { display: none !important; }
"""

WAIT_FONTS_JS = "async () => { if (document.fonts && document.fonts.ready) { await document.fonts.ready; } return document.fonts ? document.fonts.size : 0; }"

WAIT_IMAGES_JS = """
async (timeoutMs) => {
  const imgs = Array.from(document.images);
  const pending = imgs.filter(i => !i.complete || i.naturalWidth === 0);
  const waits = imgs.map(i => (i.decode ? i.decode().catch(() => {}) : Promise.resolve()));
  const timer = new Promise(r => setTimeout(r, timeoutMs));
  await Promise.race([Promise.all(waits), timer]);
  const bg = Array.from(document.querySelectorAll('*')).map(e => getComputedStyle(e).backgroundImage)
    .filter(v => v && v.startsWith('url('));
  await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
  return { images: imgs.length, pending: pending.length, backgrounds: bg.length };
}
"""

SCROLL_TO_LOAD_JS = """
async () => {
  const step = Math.max(200, Math.floor(window.innerHeight * 0.8));
  let y = 0;
  const max = () => Math.max(document.documentElement.scrollHeight, document.body ? document.body.scrollHeight : 0);
  while (y < max()) { window.scrollTo(0, y); await new Promise(r => setTimeout(r, 60)); y += step; }
  window.scrollTo(0, 0);
  await new Promise(r => setTimeout(r, 60));
  return max();
}
"""

MASK_BOXES_JS = """
(selectors) => {
  const out = [];
  for (const sel of selectors) {
    let nodes = [];
    try { nodes = Array.from(document.querySelectorAll(sel)); } catch (e) { continue; }
    nodes.forEach((el, i) => {
      const r = el.getBoundingClientRect();
      const cs = getComputedStyle(el);
      if (r.width <= 0 || r.height <= 0 || cs.visibility === 'hidden' || cs.display === 'none') return;
      out.push({ selector: sel, index: i, x: r.left + window.scrollX, y: r.top + window.scrollY, w: r.width, h: r.height });
    });
  }
  return out;
}
"""

# Bounding boxes of every visible element and text node (ignored zones excluded): an engine-independent
# measurement of layout displacement, used to label dataset pairs (see docs/DATASET.md).
GEOMETRY_JS = """
() => {
  const out = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
  let n;
  while ((n = walker.nextNode())) {
    if (n.nodeType === 1) {
      if (n.closest('[data-vrt-ignore]')) continue;
      const r = n.getBoundingClientRect();
      out.push([r.left, r.top, r.right, r.bottom]);
    } else if (n.textContent.trim() && n.parentElement && !n.parentElement.closest('[data-vrt-ignore]')) {
      const rg = document.createRange(); rg.selectNodeContents(n);
      const r = rg.getBoundingClientRect();
      out.push([r.left, r.top, r.right, r.bottom]);
    }
  }
  return out;
}
"""


@dataclass
class Stabilize:
    """How hard the capture module tries to reach a deterministic render state."""

    wait_fonts: bool = True
    wait_images: bool = True
    network_idle: bool = True
    scroll_to_load: bool = False  # trigger lazy loading (only meaningful for full-page captures)
    disable_animations: bool = True
    hide_caret: bool = True
    freeze_time: Optional[str] = "2026-01-01T12:00:00Z"  # ISO timestamp, None to keep real time
    settle_ms: int = 80  # pause between stability probes
    max_attempts: int = 5  # screenshots taken until two consecutive frames match
    images_timeout_ms: int = 4000
    navigation_timeout_ms: int = 30000


@dataclass
class CaptureSpec:
    """Everything that defines one screenshot."""

    name: str
    url: Optional[str] = None  # http(s):// or file:// URL or a local path
    viewport_width: int = 1280
    viewport_height: int = 720
    device_scale_factor: float = 1.0
    full_page: bool = False
    locale: str = "en-US"
    timezone: str = "UTC"
    color_scheme: str = "light"
    mask_selectors: List[str] = field(default_factory=list)
    mask_regions: List[Dict[str, float]] = field(default_factory=list)  # static {x,y,w,h}
    extra_css: str = ""
    pre_capture_js: str = ""
    wait_for_selector: Optional[str] = None
    stabilize: Stabilize = field(default_factory=Stabilize)
    diff_overrides: Dict[str, Any] = field(default_factory=dict)  # per-page DiffConfig overrides
    collect_geometry: bool = False  # also record element/text bounding boxes in meta["geometry"]
