"""Reporters against the mock CI server, including the merge gate (branch protection)."""
import httpx
import pytest

from ci_adapter import GitHubReporter, GitLabReporter, WebhookReporter, comment_markdown, detect_context, sign_payload
from ci_adapter.context import CiContext
from ci_adapter.render import MARKER, status_description


def summary(verdict="failed"):
    failing = verdict == "failed"
    return {
        "project": "demo", "verdict": verdict, "exit_code": 1 if failing else 0, "total": 3,
        "passed": 2 if failing else 3, "failed": 1 if failing else 0, "missing_baseline": 0, "errors": 0, "profile": "balanced",
        "timing": {"wall_s": 1.2, "capture_ms": 1, "diff_ms": 1},
        "pages": [{"page": "home@chromium/desktop", "status": "failed" if failing else "passed", "diff_pixels": 321, "max_delta_e": 14.2,
                   "regions": [{"x": 10, "y": 20, "w": 30, "h": 40, "area": 321, "max_delta_e": 14.2}]}],
    }


@pytest.fixture()
def ci(mock_ci):
    httpx.post(f"{mock_ci}/__reset")
    return mock_ci


def gh_ctx(url, sha="a" * 40, pr=1):
    return CiContext("github", "acme/web", sha, "feat", pr, url, "tok")


def create_pr(url, sha):
    r = httpx.post(f"{url}/repos/acme/web/pulls", json={"head": sha, "title": "t"}, headers={"Authorization": "Bearer tok"})
    return r.json()["number"]


def test_github_failing_run_blocks_merge_and_passing_run_unblocks(ci):
    sha = "b" * 40
    pr = create_pr(ci, sha)
    ctx = gh_ctx(ci, sha, pr)
    rep = GitHubReporter()
    rep.pending(ctx)
    assert httpx.put(f"{ci}/repos/acme/web/pulls/{pr}/merge", headers={"Authorization": "Bearer tok"}).status_code == 405  # pending
    done = rep.publish(summary("failed"), ctx, "http://reports/1")
    assert done["status"] == "failure" and done["check_run"] and done["comment"]["updated"] is False
    r = httpx.put(f"{ci}/repos/acme/web/pulls/{pr}/merge", headers={"Authorization": "Bearer tok"})
    assert r.status_code == 405 and "vrt/visual-regression" in r.json()["message"]
    # second run on the same PR updates the one comment instead of adding another
    done = rep.publish(summary("passed"), ctx, "http://reports/2")
    assert done["comment"]["updated"] is True
    st = httpx.get(f"{ci}/__state").json()
    assert len(st["comments"][f"acme/web#{pr}"]) == 1 and MARKER in st["comments"][f"acme/web#{pr}"][0]["body"]
    assert httpx.put(f"{ci}/repos/acme/web/pulls/{pr}/merge", headers={"Authorization": "Bearer tok"}).status_code == 200


def test_gitlab_reporter_and_merge_gate(ci):
    sha = "c" * 40
    mr = httpx.post(f"{ci}/api/v4/projects/7/merge_requests", json={"sha": sha}, headers={"PRIVATE-TOKEN": "t"}).json()["iid"]
    ctx = CiContext("gitlab", "7", sha, "feat", mr, f"{ci}/api/v4", "t")
    rep = GitLabReporter()
    rep.publish(summary("failed"), ctx, None)
    assert httpx.put(f"{ci}/api/v4/projects/7/merge_requests/{mr}/merge", headers={"PRIVATE-TOKEN": "t"}).status_code == 405
    rep.publish(summary("passed"), ctx, None)
    assert httpx.put(f"{ci}/api/v4/projects/7/merge_requests/{mr}/merge", headers={"PRIVATE-TOKEN": "t"}).status_code == 200


def test_missing_token_is_rejected(ci):
    ctx = CiContext("github", "acme/web", "d" * 40, "x", None, ci, None)
    from ci_adapter.reporters import CiError

    with pytest.raises(CiError, match="401"):
        GitHubReporter().publish(summary("failed"), ctx, None)


def test_webhook_signature(ci):
    import json

    body = json.dumps({"a": 1}).encode()
    sig = sign_payload("s3cret", body)
    assert sig.startswith("sha256=") and len(sig) == 71
    assert sign_payload("s3cret", body) == sig and sign_payload("other", body) != sig


def test_context_detection_github_pr(monkeypatch, tmp_path):
    import json

    ev = tmp_path / "event.json"
    ev.write_text(json.dumps({"pull_request": {"number": 42, "head": {"sha": "f" * 40}}}))
    env = {"GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": "acme/web", "GITHUB_SHA": "m" * 40, "GITHUB_REF": "refs/pull/42/merge",
           "GITHUB_EVENT_PATH": str(ev), "GITHUB_TOKEN": "t"}
    c = detect_context(env)
    assert (c.provider, c.repo, c.pr, c.sha, c.token) == ("github", "acme/web", 42, "f" * 40, "t")
    g = detect_context({"GITLAB_CI": "true", "CI_PROJECT_ID": "9", "CI_COMMIT_SHA": "e" * 40, "CI_MERGE_REQUEST_IID": "5", "GITLAB_TOKEN": "x"})
    assert (g.provider, g.repo, g.pr) == ("gitlab", "9", 5)
    assert detect_context({}).provider == "none"


def test_rendering():
    assert status_description(summary("failed")) == "1 changed of 3 screenshot(s)"
    assert "All 3" in status_description(summary("passed"))
    md = comment_markdown(summary("failed"), "http://r")
    assert MARKER in md and "home@chromium/desktop" in md and "30×40" in md and "http://r" in md
