"""HTTP API / webhook service.

    POST /api/v1/compare          multipart (baseline, candidate, [profile, overrides, regions, report, memory])
    POST /api/v1/runs             start a suite run from a server-side config (async job)
    GET  /api/v1/runs/{id}        job state / summary
    GET  /api/v1/reports/{id}/…   HTML report + artefacts of a run or comparison
    POST /webhook/ci              HMAC-signed webhook (GitHub ``pull_request`` events or a generic JSON body)
    GET  /metrics                 Prometheus exposition (diff_time_ms, memory_peak_mb, ...)
    GET  /healthz

    uvicorn ci_adapter.server:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.concurrency import run_in_threadpool
from prometheus_client import GCCollector, PlatformCollector, ProcessCollector

from core.config import ConfigError, load_config
from core.pipeline import RunOptions, compare_pair, run_test
from core.profiles import get_profile, profile_names
from diff_engine import DiffConfig, write_html_report, write_page_artifacts
from metrics import Metrics, global_metrics

from .reporters import make_reporters, publish_all

log = logging.getLogger("vrt.server")
_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_FILE = re.compile(r"^[A-Za-z0-9_./@-]{1,200}$")


class Settings:
    def __init__(self) -> None:
        env = os.environ
        self.reports_dir = Path(env.get("VRT_REPORTS_DIR", ".vrt/server-reports")).resolve()
        self.config_dir = Path(env.get("VRT_CONFIG_DIR", "examples")).resolve()
        self.default_config = env.get("VRT_DEFAULT_CONFIG", "")
        self.webhook_secret = env.get("VRT_WEBHOOK_SECRET", "")
        self.allow_unsigned = env.get("VRT_ALLOW_UNSIGNED_WEBHOOKS") == "1"
        self.max_upload = int(float(env.get("VRT_MAX_UPLOAD_MB", "25")) * 2**20)
        self.public_url = env.get("VRT_PUBLIC_URL", "").rstrip("/")


class Jobs:
    """Single-worker queue: browser runs are CPU/RAM heavy and Playwright's sync API wants one thread."""

    def __init__(self) -> None:
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="vrt-run")
        self.items: Dict[str, Dict[str, Any]] = {}
        self.lock = threading.Lock()

    def submit(self, fn, **meta) -> str:
        jid = uuid.uuid4().hex[:12]
        with self.lock:
            self.items[jid] = {"id": jid, "status": "queued", "created": time.time(), **meta}
        self.pool.submit(self._run, jid, fn)
        return jid

    def _run(self, jid: str, fn) -> None:
        self._set(jid, status="running", started=time.time())
        try:
            self._set(jid, status="done", finished=time.time(), **fn(jid))
        except Exception as exc:  # noqa: BLE001
            log.exception("job %s failed", jid)
            self._set(jid, status="error", finished=time.time(), error=f"{type(exc).__name__}: {exc}")

    def _set(self, jid: str, **kv: Any) -> None:
        with self.lock:
            self.items[jid].update(kv)

    def get(self, jid: str) -> Optional[Dict[str, Any]]:
        with self.lock:
            it = self.items.get(jid)
            return dict(it) if it else None


def create_app(settings: Optional[Settings] = None, metrics: Optional[Metrics] = None) -> FastAPI:
    st = settings or Settings()
    st.reports_dir.mkdir(parents=True, exist_ok=True)
    mx = metrics or global_metrics()
    for coll in (ProcessCollector, PlatformCollector, GCCollector):  # process_resident_memory_bytes etc.
        try:
            coll(registry=mx.registry)
        except ValueError:  # already registered (app re-created in tests)
            pass
    jobs = Jobs()
    app = FastAPI(title="Visual Regression Testing API", version="1.0")
    app.state.settings, app.state.metrics, app.state.jobs = st, mx, jobs

    def base_url(request: Request) -> str:
        return st.public_url or str(request.base_url).rstrip("/")

    # ---------------------------------------------------------------------------- health / metrics
    @app.get("/healthz")
    def healthz() -> Dict[str, Any]:
        return {"status": "ok", "profiles": profile_names()}

    @app.get("/metrics")
    def prometheus() -> Response:
        return Response(mx.render_prometheus(), media_type=mx.content_type)

    @app.get("/api/v1/metrics.json")
    def metrics_json() -> Dict[str, Any]:
        return mx.snapshot()

    # ----------------------------------------------------------------------------------- compare
    async def _read(f: UploadFile, what: str) -> bytes:
        data = await f.read(st.max_upload + 1)
        if len(data) > st.max_upload:
            raise HTTPException(413, f"{what} exceeds {st.max_upload // 2**20} MB")
        if not data:
            raise HTTPException(422, f"{what} is empty")
        return data

    @app.post("/api/v1/compare")
    async def compare(
        request: Request,
        baseline: UploadFile = File(...),
        candidate: UploadFile = File(...),
        profile: str = Form("balanced"),
        overrides: str = Form(""),
        regions: str = Form(""),
        report: bool = Form(False),
        memory: bool = Form(False),
    ) -> JSONResponse:
        a, b = await _read(baseline, "baseline"), await _read(candidate, "candidate")
        try:
            cfg = get_profile(profile)
            if overrides:
                cfg = cfg.merged(json.loads(overrides))
            reg: List[Dict[str, Any]] = json.loads(regions) if regions else []
            if not isinstance(reg, list) or any(not {"x", "y", "w", "h"} <= set(r) for r in reg):
                raise ValueError("regions must be a list of {x,y,w,h}")
        except (ConfigError, ValueError) as exc:
            raise HTTPException(422, f"invalid options: {exc}") from exc
        try:
            res, mem = await run_in_threadpool(compare_pair, a, b, cfg, reg, memory_probe=memory, keep_artifacts=report, metrics=mx)
        except Exception as exc:  # undecodable image etc.
            raise HTTPException(422, f"cannot compare images: {type(exc).__name__}: {exc}") from exc
        body: Dict[str, Any] = {"verdict": "passed" if res.passed else "failed", **res.to_dict(), "memory_peak_mb": None if mem is None else round(mem, 3)}
        if report:
            rid = uuid.uuid4().hex[:12]
            out = st.reports_dir / rid
            files = write_page_artifacts(out, "compare", res)
            entry = {"page": "compare", "status": body["verdict"], **res.to_dict(), "files": files}
            summary = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "config": f"profile {profile}", "total": 1,
                       "passed": int(res.passed), "failed": int(not res.passed), "missing_baseline": 0, "errors": 0, "verdict": body["verdict"]}
            write_html_report(out, [entry], summary, title="Image comparison")
            body["report_id"], body["report_url"] = rid, f"{base_url(request)}/api/v1/reports/{rid}/index.html"
        return JSONResponse(body)

    # ------------------------------------------------------------------------------------ reports
    @app.get("/api/v1/reports/{rid}/{path:path}")
    def report_file(rid: str, path: str = "index.html") -> FileResponse:
        if not _ID.match(rid) or not _FILE.match(path or "index.html"):
            raise HTTPException(404, "not found")
        root = (st.reports_dir / rid).resolve()
        target = (root / (path or "index.html")).resolve()
        if root not in target.parents and target != root or not target.is_file():
            raise HTTPException(404, "not found")
        return FileResponse(target)

    # --------------------------------------------------------------------------------------- runs
    def _config_path(name: str) -> Path:
        if not name or "/" in name or "\\" in name or name.startswith("."):
            raise HTTPException(422, "config must be a file name inside the server config directory")
        p = st.config_dir / name
        if not p.is_file():
            raise HTTPException(404, f"config {name!r} not found on the server")
        return p

    def _execute(request_base: str, config: str, only: Optional[List[str]], overrides: Dict[str, Any], publish: bool):
        def job(jid: str) -> Dict[str, Any]:
            cfg = load_config(_config_path(config))
            cfg.output_dir = str(st.reports_dir / jid)
            ctx, reporters = make_reporters(cfg.ci)
            for k in ("repo", "sha", "pr"):
                if overrides.get(k):
                    setattr(ctx, k, overrides[k])
            report_url = f"{request_base}/api/v1/reports/{jid}/index.html"
            ci: List[Dict[str, Any]] = []
            if publish and reporters:
                publish_all(reporters, ctx, None, report_url)
            summary = run_test(cfg, RunOptions(only=only, metrics=mx))
            if publish and reporters:
                ci = publish_all(reporters, ctx, summary, report_url)
            brief = {k: summary[k] for k in ("verdict", "exit_code", "total", "passed", "failed", "missing_baseline", "errors", "timing")}
            return {"summary": brief, "report_url": report_url, "ci": ci}

        return job

    @app.post("/api/v1/runs", status_code=202)
    async def start_run(request: Request) -> Dict[str, Any]:
        body = await request.json()
        config = str(body.get("config") or st.default_config)
        _config_path(config)
        fn = _execute(base_url(request), config, body.get("only"), {k: body.get(k) for k in ("repo", "sha", "pr")}, bool(body.get("publish", False)))
        jid = jobs.submit(fn, config=config)
        return {"id": jid, "status_url": f"{base_url(request)}/api/v1/runs/{jid}"}

    @app.get("/api/v1/runs/{jid}")
    def get_run(jid: str) -> Dict[str, Any]:
        it = jobs.get(jid) if _ID.match(jid) else None
        if not it:
            raise HTTPException(404, "unknown run")
        return it

    # ------------------------------------------------------------------------------------ webhook
    def _verify(raw: bytes, request: Request) -> None:
        sig = request.headers.get("x-hub-signature-256") or request.headers.get("x-vrt-signature") or ""
        if not st.webhook_secret:
            if st.allow_unsigned:
                return
            raise HTTPException(403, "webhooks are disabled: set VRT_WEBHOOK_SECRET")
        expected = "sha256=" + hmac.new(st.webhook_secret.encode(), raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, sig):
            raise HTTPException(401, "invalid signature")

    @app.post("/webhook/ci", status_code=202)
    async def webhook(request: Request) -> Dict[str, Any]:
        raw = await request.body()
        _verify(raw, request)
        try:
            payload = json.loads(raw or b"{}")
        except ValueError as exc:
            raise HTTPException(400, "body is not JSON") from exc
        event = request.headers.get("x-github-event", "")
        if event == "ping":
            return {"pong": True}
        overrides: Dict[str, Any] = {}
        config = payload.get("config") or st.default_config
        if event == "pull_request":
            if payload.get("action") not in ("opened", "synchronize", "reopened"):
                return {"ignored": f"pull_request/{payload.get('action')}"}
            pr = payload.get("pull_request", {})
            overrides = {"repo": payload.get("repository", {}).get("full_name"), "sha": pr.get("head", {}).get("sha"), "pr": pr.get("number")}
        elif event and event != "pull_request":
            return {"ignored": event}
        else:
            overrides = {k: payload.get(k) for k in ("repo", "sha", "pr")}
        if not config:
            raise HTTPException(422, "no config: pass {'config': name} or set VRT_DEFAULT_CONFIG")
        _config_path(str(config))
        fn = _execute(base_url(request), str(config), payload.get("only"), overrides, True)
        jid = jobs.submit(fn, config=config, trigger=event or "generic")
        return {"id": jid, "status_url": f"{base_url(request)}/api/v1/runs/{jid}"}

    return app


app = create_app()
