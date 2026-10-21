"""CI reporters: commit statuses / check runs / PR comments / signed webhooks.

All reporters talk plain REST through ``httpx`` so the same code works against github.com, GitLab,
GitHub Enterprise and the bundled ``mock-ci`` server used in the demo and the tests.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from typing import Any, Dict, List, Mapping, Optional, Tuple

import httpx

from .context import CiContext, detect_context
from .render import MARKER, check_run_output, comment_markdown, status_description, verdict_state

log = logging.getLogger("vrt.ci")
USER_AGENT = "vrt-ci-adapter/1.0"


class CiError(RuntimeError):
    pass


def _request(client: httpx.Client, method: str, url: str, retries: int = 3, **kw: Any) -> httpx.Response:
    """HTTP with retry on connection errors / 5xx / 429 (exponential back-off)."""
    last: Optional[Exception] = None
    for attempt in range(retries):
        try:
            r = client.request(method, url, **kw)
            if r.status_code in (429,) or r.status_code >= 500:
                last = CiError(f"{method} {url} -> HTTP {r.status_code}")
            else:
                if r.status_code >= 400:
                    raise CiError(f"{method} {url} -> HTTP {r.status_code}: {r.text[:300]}")
                return r
        except httpx.HTTPError as exc:
            last = exc
        time.sleep(0.4 * (2**attempt))
    raise CiError(f"{method} {url} failed after {retries} attempts: {last}")


class Reporter:
    name = "base"

    def pending(self, ctx: CiContext, description: str = "Visual regression tests running") -> Dict[str, Any]:
        return {}

    def publish(self, summary: Dict[str, Any], ctx: CiContext, report_url: Optional[str]) -> Dict[str, Any]:
        raise NotImplementedError


# --------------------------------------------------------------------------------- GitHub
class GitHubReporter(Reporter):
    name = "github"

    def __init__(self, context: str = "vrt/visual-regression", comment: bool = True, check_run: bool = True, client: Optional[httpx.Client] = None):
        self.context, self.comment, self.check_run = context, comment, check_run
        self._client = client

    def _c(self, ctx: CiContext) -> httpx.Client:
        if self._client is not None:
            return self._client
        headers = {"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT, "X-GitHub-Api-Version": "2022-11-28"}
        if ctx.token:
            headers["Authorization"] = f"Bearer {ctx.token}"
        return httpx.Client(base_url=ctx.api_url.rstrip("/"), headers=headers, timeout=15.0)

    def _status(self, c: httpx.Client, ctx: CiContext, state: str, description: str, url: Optional[str]) -> Dict[str, Any]:
        body = {"state": state, "context": self.context, "description": description[:140]}
        if url:
            body["target_url"] = url
        return _request(c, "POST", f"/repos/{ctx.repo}/statuses/{ctx.sha}", json=body).json()

    def pending(self, ctx: CiContext, description: str = "Visual regression tests running") -> Dict[str, Any]:
        self._require(ctx)
        with self._c(ctx) as c:
            self._status(c, ctx, "pending", description, ctx.run_url)
        return {"status": "pending"}

    @staticmethod
    def _require(ctx: CiContext) -> None:
        if not (ctx.repo and ctx.sha):
            raise CiError("GitHub reporter needs repo and sha (GITHUB_REPOSITORY/GITHUB_SHA or VRT_REPO/VRT_SHA)")

    def publish(self, summary: Dict[str, Any], ctx: CiContext, report_url: Optional[str]) -> Dict[str, Any]:
        self._require(ctx)
        done: Dict[str, Any] = {}
        link = report_url or ctx.run_url
        with self._c(ctx) as c:
            self._status(c, ctx, verdict_state(summary), status_description(summary), link)
            done["status"] = verdict_state(summary)
            if self.check_run:
                conclusion = {"passed": "success", "failed": "failure", "error": "failure"}[summary["verdict"]]
                body = {
                    "name": self.context,
                    "head_sha": ctx.sha,
                    "status": "completed",
                    "conclusion": conclusion,
                    "output": check_run_output(summary, report_url),
                }
                if link:
                    body["details_url"] = link
                done["check_run"] = _request(c, "POST", f"/repos/{ctx.repo}/check-runs", json=body).json().get("id")
            if self.comment and ctx.pr:
                done["comment"] = self._upsert_comment(c, ctx, comment_markdown(summary, report_url, ctx.run_url))
        return done

    def _upsert_comment(self, c: httpx.Client, ctx: CiContext, body: str) -> Any:
        for page in range(1, 4):
            r = _request(c, "GET", f"/repos/{ctx.repo}/issues/{ctx.pr}/comments", params={"per_page": 100, "page": page}).json()
            for cm in r:
                if MARKER in (cm.get("body") or ""):
                    _request(c, "PATCH", f"/repos/{ctx.repo}/issues/comments/{cm['id']}", json={"body": body})
                    return {"id": cm["id"], "updated": True}
            if len(r) < 100:
                break
        created = _request(c, "POST", f"/repos/{ctx.repo}/issues/{ctx.pr}/comments", json={"body": body}).json()
        return {"id": created.get("id"), "updated": False}


# --------------------------------------------------------------------------------- GitLab
class GitLabReporter(Reporter):
    name = "gitlab"
    _STATE = {"success": "success", "failure": "failed", "error": "failed", "pending": "running"}

    def __init__(self, context: str = "vrt/visual-regression", comment: bool = True, client: Optional[httpx.Client] = None):
        self.context, self.comment = context, comment
        self._client = client

    def _c(self, ctx: CiContext) -> httpx.Client:
        if self._client is not None:
            return self._client
        headers = {"User-Agent": USER_AGENT}
        if ctx.token:
            headers["PRIVATE-TOKEN"] = ctx.token
        return httpx.Client(base_url=ctx.api_url.rstrip("/"), headers=headers, timeout=15.0)

    def _status(self, c: httpx.Client, ctx: CiContext, state: str, description: str, url: Optional[str]) -> Any:
        body = {"state": self._STATE[state], "name": self.context, "description": description[:140]}
        if url:
            body["target_url"] = url
        return _request(c, "POST", f"/projects/{ctx.repo}/statuses/{ctx.sha}", json=body).json()

    def pending(self, ctx: CiContext, description: str = "Visual regression tests running") -> Dict[str, Any]:
        if not (ctx.repo and ctx.sha):
            raise CiError("GitLab reporter needs project id and sha")
        with self._c(ctx) as c:
            self._status(c, ctx, "pending", description, ctx.run_url)
        return {"status": "running"}

    def publish(self, summary: Dict[str, Any], ctx: CiContext, report_url: Optional[str]) -> Dict[str, Any]:
        if not (ctx.repo and ctx.sha):
            raise CiError("GitLab reporter needs project id and sha")
        done: Dict[str, Any] = {}
        with self._c(ctx) as c:
            self._status(c, ctx, verdict_state(summary), status_description(summary), report_url or ctx.run_url)
            done["status"] = self._STATE[verdict_state(summary)]
            if self.comment and ctx.pr:
                body = comment_markdown(summary, report_url, ctx.run_url)
                notes = _request(c, "GET", f"/projects/{ctx.repo}/merge_requests/{ctx.pr}/notes", params={"per_page": 100}).json()
                existing = next((n for n in notes if MARKER in (n.get("body") or "")), None)
                if existing:
                    _request(c, "PUT", f"/projects/{ctx.repo}/merge_requests/{ctx.pr}/notes/{existing['id']}", json={"body": body})
                    done["comment"] = {"id": existing["id"], "updated": True}
                else:
                    n = _request(c, "POST", f"/projects/{ctx.repo}/merge_requests/{ctx.pr}/notes", json={"body": body}).json()
                    done["comment"] = {"id": n.get("id"), "updated": False}
        return done


# --------------------------------------------------------------------------------- webhook
def sign_payload(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class WebhookReporter(Reporter):
    """POST a compact JSON event to any URL; the body is signed (HMAC-SHA256) like GitHub webhooks."""

    name = "webhook"

    def __init__(self, url: str, secret: Optional[str] = None, client: Optional[httpx.Client] = None):
        self.url, self.secret, self._client = url, secret, client

    def _post(self, event: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        body = json.dumps({"event": event, **payload}, ensure_ascii=False).encode()
        headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT, "X-VRT-Event": event}
        if self.secret:
            headers["X-VRT-Signature"] = sign_payload(self.secret, body)
        c = self._client or httpx.Client(timeout=15.0)
        try:
            r = _request(c, "POST", self.url, content=body, headers=headers)
        finally:
            if self._client is None:
                c.close()
        return {"http": r.status_code}

    def pending(self, ctx: CiContext, description: str = "running") -> Dict[str, Any]:
        return self._post("vrt.run.started", {"repo": ctx.repo, "sha": ctx.sha, "pr": ctx.pr})

    def publish(self, summary: Dict[str, Any], ctx: CiContext, report_url: Optional[str]) -> Dict[str, Any]:
        brief = {k: summary[k] for k in ("project", "verdict", "exit_code", "total", "passed", "failed", "missing_baseline", "errors", "profile", "timing")}
        failing = [
            {"page": p["page"], "status": p["status"], "diff_pixels": p.get("diff_pixels"), "regions": (p.get("regions") or [])[:5]}
            for p in summary["pages"]
            if p["status"] != "passed"
        ]
        return self._post(
            "vrt.run.completed",
            {"repo": ctx.repo, "sha": ctx.sha, "pr": ctx.pr, "report_url": report_url, "state": verdict_state(summary), "summary": brief, "failing": failing},
        )


class StdoutReporter(Reporter):
    name = "stdout"

    def publish(self, summary: Dict[str, Any], ctx: CiContext, report_url: Optional[str]) -> Dict[str, Any]:
        print(f"[ci] {verdict_state(summary)}: {status_description(summary)}" + (f" -> {report_url}" if report_url else ""))
        return {"printed": True}


# ---------------------------------------------------------------------------------- factory
def make_reporters(ci_cfg: Any, env: Optional[Mapping[str, str]] = None) -> Tuple[CiContext, List[Reporter]]:
    """Build the context + reporters described by a ``CiCfg`` (``provider: auto`` detects from env)."""
    env = os.environ if env is None else env
    ctx = detect_context(env, ci_cfg.provider, ci_cfg.api_url, ci_cfg.token_env)
    reporters: List[Reporter] = []
    if ctx.provider == "github":
        reporters.append(GitHubReporter(ci_cfg.context, ci_cfg.comment, ci_cfg.check_run))
    elif ctx.provider == "gitlab":
        reporters.append(GitLabReporter(ci_cfg.context, ci_cfg.comment))
    if ci_cfg.webhook_url or ctx.provider == "webhook":
        url = ci_cfg.webhook_url or env.get("VRT_WEBHOOK_URL", "")
        if url:
            reporters.append(WebhookReporter(url, env.get(ci_cfg.webhook_secret_env)))
    return ctx, reporters


def publish_all(reporters: List[Reporter], ctx: CiContext, summary: Optional[Dict[str, Any]], report_url: Optional[str]) -> List[Dict[str, Any]]:
    """Call every reporter; failures are logged and returned (a flaky status API must not fail the test run)."""
    out: List[Dict[str, Any]] = []
    for r in reporters:
        try:
            res = r.publish(summary, ctx, report_url) if summary is not None else r.pending(ctx)
            out.append({"reporter": r.name, "ok": True, **res})
        except Exception as exc:  # noqa: BLE001
            log.error("%s reporter failed: %s", r.name, exc)
            out.append({"reporter": r.name, "ok": False, "error": str(exc)})
    return out
