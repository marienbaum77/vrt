import hashlib
import hmac
import io
import json

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from ci_adapter.server import Settings, create_app
from metrics import Metrics

from conftest import make_ui


def png(arr) -> bytes:
    b = io.BytesIO()
    Image.fromarray(arr).save(b, "PNG")
    return b.getvalue()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("VRT_REPORTS_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv("VRT_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("VRT_WEBHOOK_SECRET", "topsecret")
    monkeypatch.setenv("VRT_MAX_UPLOAD_MB", "1")
    return TestClient(create_app(Settings(), Metrics()))


def test_health_and_compare_with_report_and_metrics(client):
    assert client.get("/healthz").json()["status"] == "ok"
    a = make_ui()
    b = a.copy()
    b[40:100, 16:150] = (200, 30, 30)
    r = client.post("/api/v1/compare", files={"baseline": ("a.png", png(a)), "candidate": ("b.png", png(b))}, data={"report": "true", "memory": "true"})
    body = r.json()
    assert r.status_code == 200 and body["verdict"] == "failed" and body["diff_pixels"] > 1000 and body["memory_peak_mb"] > 0
    page = client.get(f"/api/v1/reports/{body['report_id']}/index.html")
    assert page.status_code == 200 and "<html" in page.text
    same = client.post("/api/v1/compare", files={"baseline": ("a.png", png(a)), "candidate": ("a.png", png(a))}).json()
    assert same["verdict"] == "passed"
    text = client.get("/metrics").text
    for name in ("vrt_diff_time_ms_bucket", "vrt_memory_peak_mb_bucket", 'vrt_comparisons_total{result="failed"}', "process_resident_memory_bytes"):
        assert name in text, name


def test_compare_validation(client):
    good = png(make_ui())
    assert client.post("/api/v1/compare", files={"baseline": ("a.png", b"not an image"), "candidate": ("b.png", good)}).status_code == 422
    assert client.post("/api/v1/compare", files={"baseline": ("a.png", good), "candidate": ("b.png", good)}, data={"profile": "nope"}).status_code == 422
    assert client.post("/api/v1/compare", files={"baseline": ("a.png", good), "candidate": ("b.png", good)}, data={"overrides": "{bad"}).status_code == 422
    big = png(np.random.default_rng(0).integers(0, 256, (900, 900, 3), dtype=np.uint8))  # > 1 MB of incompressible noise
    assert client.post("/api/v1/compare", files={"baseline": ("a.png", big), "candidate": ("b.png", good)}).status_code == 413


def test_report_path_traversal_is_blocked(client):
    assert client.get("/api/v1/reports/..%2f..%2fetc/passwd").status_code == 404
    assert client.get("/api/v1/reports/abc123/../../../etc/passwd").status_code in (404, 400)
    assert client.get("/api/v1/reports/nonexistent/index.html").status_code == 404


def _sig(body: bytes, secret="topsecret") -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_webhook_requires_valid_signature(client):
    body = json.dumps({"zen": "x"}).encode()
    assert client.post("/webhook/ci", content=body).status_code == 401
    assert client.post("/webhook/ci", content=body, headers={"X-Hub-Signature-256": _sig(body, "wrong")}).status_code == 401
    ok = client.post("/webhook/ci", content=body, headers={"X-Hub-Signature-256": _sig(body), "X-GitHub-Event": "ping"})
    assert ok.status_code == 202 and ok.json() == {"pong": True}
    pr = json.dumps({"action": "closed", "pull_request": {}}).encode()
    r = client.post("/webhook/ci", content=pr, headers={"X-Hub-Signature-256": _sig(pr), "X-GitHub-Event": "pull_request"})
    assert r.json()["ignored"] == "pull_request/closed"


def test_webhook_disabled_without_secret(tmp_path, monkeypatch):
    monkeypatch.delenv("VRT_WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("VRT_REPORTS_DIR", str(tmp_path))
    c = TestClient(create_app(Settings(), Metrics()))
    assert c.post("/webhook/ci", content=b"{}").status_code == 403


def test_run_config_must_be_plain_filename(client):
    assert client.post("/api/v1/runs", json={"config": "../x.yaml"}).status_code == 422
    assert client.post("/api/v1/runs", json={"config": "missing.yaml"}).status_code == 404
