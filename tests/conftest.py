"""Shared fixtures: synthetic UI images, a local mock-CI server, optional browser availability."""
from __future__ import annotations

import socket
import threading
import time
from pathlib import Path

import numpy as np
import pytest
from PIL import Image


def make_ui(w: int = 320, h: int = 200, seed: int = 0) -> np.ndarray:
    """A small UI-like image: header bar, cards and 'text' stripes with anti-aliased edges."""
    rng = np.random.default_rng(seed)
    img = np.full((h, w, 3), 255, dtype=np.uint8)
    img[:28] = (30, 64, 175)
    img[40:100, 16:150] = (240, 244, 255)
    img[40:100, 170:300] = (254, 242, 242)
    for y in range(116, h - 8, 14):  # text lines
        x = 16
        while x < w - 40:
            ww = int(rng.integers(8, 40))
            img[y : y + 6, x : x + ww] = (40, 40, 48)
            x += ww + 6
    return img


def save_png(arr: np.ndarray, path: Path) -> Path:
    Image.fromarray(arr).save(path)
    return path


@pytest.fixture()
def ui() -> np.ndarray:
    return make_ui()


@pytest.fixture(scope="session")
def mock_ci():
    """Run the mock GitHub/GitLab server on a free port in a background thread."""
    import uvicorn

    from ci_adapter.mock_ci import STATE, app

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    STATE.reset()
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    th.join(timeout=5)


def _browser_ok() -> bool:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            p.chromium.launch().close()
        return True
    except Exception:  # noqa: BLE001
        return False


@pytest.fixture(scope="session")
def browser_available() -> bool:
    return _browser_ok()


@pytest.fixture()
def need_browser(browser_available):
    if not browser_available:
        pytest.skip("chromium is not available")
