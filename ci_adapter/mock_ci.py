"""Mock GitHub / GitLab API with *branch protection*, used by the demo stand and the tests.

It implements just enough of both REST APIs for the reporters (commit statuses, check runs,
issue / MR comments, merge) and enforces the rule that matters for the project brief:
**a pull request whose required status check is not ``success`` cannot be merged.**

    uvicorn ci_adapter.mock_ci:app --port 8099
"""
from __future__ import annotations

import itertools
import threading
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

REQUIRED_DEFAULT = ["vrt/visual-regression"]


class State:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        self.ids = itertools.count(1)
        self.statuses: List[Dict[str, Any]] = []  # newest last
        self.check_runs: List[Dict[str, Any]] = []
        self.comments: Dict[str, List[Dict[str, Any]]] = {}  # "repo#pr" -> comments
        self.prs: Dict[str, Dict[str, Any]] = {}  # "repo#n" -> pr
        self.required: List[str] = list(REQUIRED_DEFAULT)
        self.log: List[str] = []


STATE = State()
app = FastAPI(title="mock-ci", version="1.0")


def auth(authorization: Optional[str] = Header(None), private_token: Optional[str] = Header(None, alias="PRIVATE-TOKEN")) -> str:
    token = None
    if authorization:
        token = authorization.split(" ", 1)[-1].strip()
    token = token or private_token
    if not token:
        raise HTTPException(401, "Requires authentication")
    return token


def _latest_state(repo: str, sha: str, context: str) -> Optional[str]:
    """Latest conclusion for a context on a commit (statuses and check runs both count)."""
    best: Optional[Dict[str, Any]] = None
    for s in STATE.statuses:
        if s["repo"] == repo and s["sha"] == sha and s["context"] == context:
            best = s
    for c in STATE.check_runs:
        if c["repo"] == repo and c["head_sha"] == sha and c["name"] == context and c["status"] == "completed":
            if best is None or c["id"] > best["id"]:
                best = {"id": c["id"], "state": "success" if c["conclusion"] == "success" else "failure"}
    return best["state"] if best else None


def _merge_blockers(repo: str, sha: str) -> List[str]:
    out = []
    for ctx in STATE.required:
        st = _latest_state(repo, sha, ctx)
        if st != "success":
            out.append(f'Required status check "{ctx}" is {"failing" if st in ("failure", "error", "failed") else "expected"}.')
    return out


# ---------------------------------------------------------------------------------- admin
@app.get("/healthz")
def healthz() -> Dict[str, str]:
    return {"status": "ok"}


@app.post("/__reset")
def reset() -> Dict[str, str]:
    with STATE.lock:
        STATE.reset()
    return {"status": "reset"}


@app.put("/__protection")
async def protection(request: Request) -> Dict[str, Any]:
    body = await request.json()
    STATE.required = list(body.get("contexts", []))
    return {"required": STATE.required}


@app.get("/__state")
def state() -> Dict[str, Any]:
    return {
        "required": STATE.required,
        "statuses": STATE.statuses,
        "check_runs": STATE.check_runs,
        "comments": STATE.comments,
        "prs": STATE.prs,
    }


# ---------------------------------------------------------------------------------- GitHub
@app.post("/repos/{owner}/{repo}/statuses/{sha}", status_code=201)
async def gh_status(owner: str, repo: str, sha: str, request: Request, _: str = Depends(auth)) -> Dict[str, Any]:
    body = await request.json()
    if body.get("state") not in ("error", "failure", "pending", "success"):
        raise HTTPException(422, "state must be one of error, failure, pending, success")
    with STATE.lock:
        item = {"id": next(STATE.ids), "repo": f"{owner}/{repo}", "sha": sha, "state": body["state"], "context": body.get("context", "default"),
                "description": body.get("description", ""), "target_url": body.get("target_url")}
        STATE.statuses.append(item)
    return item


@app.post("/repos/{owner}/{repo}/check-runs", status_code=201)
async def gh_check_run(owner: str, repo: str, request: Request, _: str = Depends(auth)) -> Dict[str, Any]:
    body = await request.json()
    for k in ("name", "head_sha"):
        if k not in body:
            raise HTTPException(422, f"{k} is required")
    with STATE.lock:
        item = {"id": next(STATE.ids), "repo": f"{owner}/{repo}", **body}
        STATE.check_runs.append(item)
    return item


@app.get("/repos/{owner}/{repo}/issues/{number}/comments")
def gh_list_comments(owner: str, repo: str, number: int, page: int = 1, per_page: int = 30, _: str = Depends(auth)) -> List[Dict[str, Any]]:
    items = STATE.comments.get(f"{owner}/{repo}#{number}", [])
    return items[(page - 1) * per_page : page * per_page]


@app.post("/repos/{owner}/{repo}/issues/{number}/comments", status_code=201)
async def gh_add_comment(owner: str, repo: str, number: int, request: Request, _: str = Depends(auth)) -> Dict[str, Any]:
    body = await request.json()
    with STATE.lock:
        c = {"id": next(STATE.ids), "body": body.get("body", ""), "pr": number}
        STATE.comments.setdefault(f"{owner}/{repo}#{number}", []).append(c)
    return c


@app.patch("/repos/{owner}/{repo}/issues/comments/{cid}")
async def gh_edit_comment(owner: str, repo: str, cid: int, request: Request, _: str = Depends(auth)) -> Dict[str, Any]:
    body = await request.json()
    for key, items in STATE.comments.items():
        if key.startswith(f"{owner}/{repo}#"):
            for c in items:
                if c["id"] == cid:
                    c["body"] = body.get("body", c["body"])
                    return c
    raise HTTPException(404, "comment not found")


@app.post("/repos/{owner}/{repo}/pulls", status_code=201)
async def gh_create_pr(owner: str, repo: str, request: Request, _: str = Depends(auth)) -> Dict[str, Any]:
    body = await request.json()
    with STATE.lock:
        n = sum(1 for k in STATE.prs if k.startswith(f"{owner}/{repo}#")) + 1
        pr = {"number": n, "title": body.get("title", ""), "head": {"sha": body["head"]}, "base": body.get("base", "main"), "state": "open", "merged": False}
        STATE.prs[f"{owner}/{repo}#{n}"] = pr
    return pr


@app.get("/repos/{owner}/{repo}/pulls/{number}")
def gh_get_pr(owner: str, repo: str, number: int, _: str = Depends(auth)) -> Dict[str, Any]:
    pr = STATE.prs.get(f"{owner}/{repo}#{number}")
    if not pr:
        raise HTTPException(404, "Not Found")
    blockers = _merge_blockers(f"{owner}/{repo}", pr["head"]["sha"])
    return {**pr, "mergeable": not blockers, "mergeable_state": "clean" if not blockers else "blocked", "blockers": blockers}


@app.put("/repos/{owner}/{repo}/pulls/{number}/merge")
def gh_merge(owner: str, repo: str, number: int, _: str = Depends(auth)) -> JSONResponse:
    pr = STATE.prs.get(f"{owner}/{repo}#{number}")
    if not pr:
        raise HTTPException(404, "Not Found")
    blockers = _merge_blockers(f"{owner}/{repo}", pr["head"]["sha"])
    if blockers:  # GitHub answers 405 "Method Not Allowed" with an explanatory message
        return JSONResponse({"message": "Pull Request is not mergeable: " + " ".join(blockers)}, status_code=405)
    pr["merged"], pr["state"] = True, "closed"
    return JSONResponse({"merged": True, "message": "Pull Request successfully merged"})


# ---------------------------------------------------------------------------------- GitLab
@app.post("/api/v4/projects/{pid}/statuses/{sha}", status_code=201)
async def gl_status(pid: str, sha: str, request: Request, _: str = Depends(auth)) -> Dict[str, Any]:
    body = await request.json()
    if body.get("state") not in ("pending", "running", "success", "failed", "canceled"):
        raise HTTPException(400, "state does not have a valid value")
    state_ = "success" if body["state"] == "success" else ("pending" if body["state"] in ("pending", "running") else "failure")
    with STATE.lock:
        item = {"id": next(STATE.ids), "repo": pid, "sha": sha, "state": state_, "context": body.get("name", "default"),
                "description": body.get("description", ""), "target_url": body.get("target_url")}
        STATE.statuses.append(item)
    return item


@app.get("/api/v4/projects/{pid}/merge_requests/{iid}/notes")
def gl_notes(pid: str, iid: int, _: str = Depends(auth)) -> List[Dict[str, Any]]:
    return STATE.comments.get(f"{pid}#{iid}", [])


@app.post("/api/v4/projects/{pid}/merge_requests/{iid}/notes", status_code=201)
async def gl_add_note(pid: str, iid: int, request: Request, _: str = Depends(auth)) -> Dict[str, Any]:
    body = await request.json()
    with STATE.lock:
        c = {"id": next(STATE.ids), "body": body.get("body", "")}
        STATE.comments.setdefault(f"{pid}#{iid}", []).append(c)
    return c


@app.put("/api/v4/projects/{pid}/merge_requests/{iid}/notes/{nid}")
async def gl_edit_note(pid: str, iid: int, nid: int, request: Request, _: str = Depends(auth)) -> Dict[str, Any]:
    body = await request.json()
    for c in STATE.comments.get(f"{pid}#{iid}", []):
        if c["id"] == nid:
            c["body"] = body.get("body", c["body"])
            return c
    raise HTTPException(404, "404 Not found")


@app.post("/api/v4/projects/{pid}/merge_requests", status_code=201)
async def gl_create_mr(pid: str, request: Request, _: str = Depends(auth)) -> Dict[str, Any]:
    body = await request.json()
    with STATE.lock:
        n = sum(1 for k in STATE.prs if k.startswith(f"{pid}#")) + 1
        mr = {"iid": n, "title": body.get("title", ""), "sha": body["sha"], "state": "opened"}
        STATE.prs[f"{pid}#{n}"] = {"number": n, "head": {"sha": body["sha"]}, "state": "open", "merged": False}
    return mr


@app.put("/api/v4/projects/{pid}/merge_requests/{iid}/merge")
def gl_merge(pid: str, iid: int, _: str = Depends(auth)) -> JSONResponse:
    pr = STATE.prs.get(f"{pid}#{iid}")
    if not pr:
        raise HTTPException(404, "404 Not found")
    blockers = _merge_blockers(pid, pr["head"]["sha"])
    if blockers:  # GitLab: 405 when the merge request cannot be merged (pipeline must succeed)
        return JSONResponse({"message": "405 Method Not Allowed: " + " ".join(blockers)}, status_code=405)
    pr["merged"], pr["state"] = True, "closed"
    return JSONResponse({"state": "merged"})
