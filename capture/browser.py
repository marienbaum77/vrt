"""Headless-browser screenshot capture with render stabilisation (Playwright)."""
from __future__ import annotations

import io
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import numpy as np
from PIL import Image

from .stabilize import (
    FREEZE_TIME_JS,
    GEOMETRY_JS,
    MASK_BOXES_JS,
    SCROLL_TO_LOAD_JS,
    STABILISE_CSS,
    WAIT_FONTS_JS,
    WAIT_IMAGES_JS,
    CaptureSpec,
)

SUPPORTED_BROWSERS = ("chromium", "firefox", "webkit")


@dataclass
class CaptureResult:
    name: str
    png: bytes
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def regions(self) -> List[Dict[str, float]]:
        return list(self.meta.get("regions", []))

    def save(self, directory: Path) -> Dict[str, Path]:
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        png_path = directory / f"{self.name}.png"
        meta_path = directory / f"{self.name}.meta.json"
        png_path.write_bytes(self.png)
        import json

        meta_path.write_text(json.dumps(self.meta, indent=2, ensure_ascii=False))
        return {"png": png_path, "meta": meta_path}


class CaptureError(RuntimeError):
    pass


def _to_url(url: str) -> str:
    if "://" in url:
        return url
    p = Path(url).expanduser().resolve()
    if not p.exists():
        raise CaptureError(f"local page not found: {url}")
    return p.as_uri()


_PW_LOCK = threading.Lock()
_PW_STATE: Dict[str, Any] = {"pw": None, "refs": 0}


def _acquire_playwright():
    """Playwright's sync API allows a single driver per thread: share it between sessions."""
    from playwright.sync_api import sync_playwright

    with _PW_LOCK:
        if _PW_STATE["pw"] is None:
            _PW_STATE["pw"] = sync_playwright().start()
        _PW_STATE["refs"] += 1
        return _PW_STATE["pw"]


def _release_playwright() -> None:
    with _PW_LOCK:
        _PW_STATE["refs"] -= 1
        if _PW_STATE["refs"] <= 0 and _PW_STATE["pw"] is not None:
            _PW_STATE["pw"].stop()
            _PW_STATE["pw"] = None
            _PW_STATE["refs"] = 0


class CaptureSession:
    """One browser instance that can take many screenshots.

    ``browser`` is chromium | firefox | webkit.  ``launch_args`` and ``channel`` allow
    rendering-pipeline variants (used by the dataset generator to model environment noise).
    """

    def __init__(
        self,
        browser: str = "chromium",
        headless: bool = True,
        channel: Optional[str] = None,
        launch_args: Optional[Sequence[str]] = None,
        executable_path: Optional[str] = None,
    ):
        if browser not in SUPPORTED_BROWSERS:
            raise CaptureError(f"unsupported browser {browser!r}; choose from {SUPPORTED_BROWSERS}")
        self.browser_name = browser
        self._opts: Dict[str, Any] = {"headless": headless}
        if channel:
            self._opts["channel"] = channel
        if launch_args:
            self._opts["args"] = list(launch_args)
        if executable_path:
            self._opts["executable_path"] = executable_path
        self._pw = None
        self._browser = None
        self.browser_version = ""

    # ----------------------------------------------------------- lifecycle
    def __enter__(self) -> "CaptureSession":
        self._pw = _acquire_playwright()
        try:
            self._browser = getattr(self._pw, self.browser_name).launch(**self._opts)
        except Exception as exc:  # pragma: no cover - environment dependent
            _release_playwright()
            self._pw = None
            raise CaptureError(
                f"could not launch {self.browser_name}: {exc}\nInstall with: python -m playwright install {self.browser_name}"
            ) from exc
        self.browser_version = self._browser.version
        return self

    def __exit__(self, *exc) -> None:
        try:
            if self._browser:
                self._browser.close()
        finally:
            if self._pw:
                _release_playwright()
            self._browser = self._pw = None

    # --------------------------------------------------------------- probing
    def probe(self, html: str, js: str, arg: Any = None, viewport=(1280, 720)) -> Any:
        """Load ``html`` in a throw-away page (same stabilising CSS as captures) and evaluate ``js``."""
        if self._browser is None:
            raise CaptureError("CaptureSession must be used as a context manager")
        ctx = self._browser.new_context(viewport={"width": viewport[0], "height": viewport[1]})
        try:
            page = ctx.new_page()
            page.set_content(html, wait_until="load")
            page.add_style_tag(content=STABILISE_CSS)
            page.evaluate(WAIT_FONTS_JS)
            return page.evaluate(js, arg)
        finally:
            ctx.close()

    # ------------------------------------------------------------- capture
    def capture(self, spec: CaptureSpec, html: Optional[str] = None) -> CaptureResult:
        """Capture ``spec.url`` (or the given ``html`` string) and return PNG + metadata."""
        if self._browser is None:
            raise CaptureError("CaptureSession must be used as a context manager")
        if html is None and not spec.url:
            raise CaptureError(f"spec {spec.name!r} has neither url nor html")
        st = spec.stabilize
        timings: Dict[str, float] = {}
        t0 = time.perf_counter()

        ctx_kwargs: Dict[str, Any] = {
            "viewport": {"width": spec.viewport_width, "height": spec.viewport_height},
            "device_scale_factor": spec.device_scale_factor,
            "locale": spec.locale,
            "timezone_id": spec.timezone,
            "color_scheme": spec.color_scheme,
            "reduced_motion": "reduce" if st.disable_animations else "no-preference",
        }
        ctx = self._browser.new_context(**ctx_kwargs)
        try:
            if st.freeze_time:
                ts = int(datetime.fromisoformat(st.freeze_time.replace("Z", "+00:00")).timestamp() * 1000)
                ctx.add_init_script(FREEZE_TIME_JS % {"ts": ts})
            page = ctx.new_page()
            page.set_default_timeout(st.navigation_timeout_ms)

            if html is not None:
                page.set_content(html, wait_until="load")
            else:
                page.goto(_to_url(spec.url), wait_until="load")
            if st.network_idle and html is None:
                try:
                    page.wait_for_load_state("networkidle", timeout=min(st.navigation_timeout_ms, 8000))
                except Exception:
                    pass  # long-polling pages never go idle; stability probe below still guards us
            timings["navigate"] = _ms(t0)

            css = (STABILISE_CSS if st.disable_animations else "") + (spec.extra_css or "")
            if css:
                page.add_style_tag(content=css)
            if spec.wait_for_selector:
                page.wait_for_selector(spec.wait_for_selector, state="visible")
            if st.scroll_to_load:
                page.evaluate(SCROLL_TO_LOAD_JS)
            if st.wait_fonts:
                page.evaluate(WAIT_FONTS_JS)
            img_info: Dict[str, Any] = {}
            if st.wait_images:
                img_info = page.evaluate(WAIT_IMAGES_JS, st.images_timeout_ms)
            if spec.pre_capture_js:
                page.evaluate(spec.pre_capture_js)
            geometry = page.evaluate(GEOMETRY_JS) if spec.collect_geometry else None
            timings["stabilise"] = _ms(t0) - timings["navigate"]

            shot_kwargs = dict(type="png", full_page=spec.full_page, scale="device")
            if st.disable_animations:
                shot_kwargs["animations"] = "disabled"
            if st.hide_caret:
                shot_kwargs["caret"] = "hide"

            frames = 0
            stable = False
            png = page.screenshot(**shot_kwargs)
            frames += 1
            while frames < max(1, st.max_attempts):
                page.wait_for_timeout(st.settle_ms)
                nxt = page.screenshot(**shot_kwargs)
                frames += 1
                same = _same_pixels(png, nxt)
                png = nxt
                if same:
                    stable = True
                    break
            if st.max_attempts <= 1:
                stable = True
            timings["screenshot"] = _ms(t0) - timings["navigate"] - timings["stabilise"]

            boxes: List[Dict[str, Any]] = []
            if spec.mask_selectors:
                boxes = page.evaluate(MASK_BOXES_JS, list(spec.mask_selectors))
            for r in spec.mask_regions:
                boxes.append({"selector": "static", "index": 0, **{k: float(r[k]) for k in ("x", "y", "w", "h")}})

            with Image.open(io.BytesIO(png)) as im:
                size = list(im.size)
            scale = spec.device_scale_factor
            regions = [
                {
                    **b,
                    "x": round(b["x"] * scale, 2),
                    "y": round(b["y"] * scale, 2),
                    "w": round(b["w"] * scale, 2),
                    "h": round(b["h"] * scale, 2),
                }
                for b in boxes
            ]
            meta = {
                "name": spec.name,
                "url": spec.url,
                "browser": self.browser_name,
                "browser_version": self.browser_version,
                "viewport": [spec.viewport_width, spec.viewport_height],
                "device_scale_factor": spec.device_scale_factor,
                "full_page": spec.full_page,
                "image_size": size,
                "stable": stable,
                "frames_taken": frames,
                "images": img_info,
                "regions": regions,
                "timings_ms": {k: round(v, 1) for k, v in timings.items()},
                "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            if geometry is not None:
                meta["geometry"] = geometry
            return CaptureResult(spec.name, png, meta)
        finally:
            ctx.close()


def _ms(t0: float) -> float:
    return (time.perf_counter() - t0) * 1000.0


def _same_pixels(a: bytes, b: bytes) -> bool:
    if a == b:
        return True
    with Image.open(io.BytesIO(a)) as ia, Image.open(io.BytesIO(b)) as ib:
        if ia.size != ib.size:
            return False
        return bool(np.array_equal(np.asarray(ia.convert("RGB")), np.asarray(ib.convert("RGB"))))
