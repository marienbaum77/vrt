"""Detect the CI environment (GitHub Actions / GitLab CI / generic ``VRT_*`` variables)."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Mapping, Optional


@dataclass
class CiContext:
    provider: str  # github | gitlab | none
    repo: str = ""  # "owner/repo" (GitHub) or project id (GitLab)
    sha: str = ""
    branch: str = ""
    pr: Optional[int] = None  # PR / MR number
    api_url: str = ""
    token: Optional[str] = None
    run_url: Optional[str] = None

    def describe(self) -> str:
        return f"{self.provider}:{self.repo}@{self.sha[:8]}" + (f"#{self.pr}" if self.pr else "")


def _int(v: Optional[str]) -> Optional[int]:
    try:
        return int(v) if v else None
    except ValueError:
        return None


def detect_provider(env: Mapping[str, str]) -> str:
    forced = env.get("VRT_CI_PROVIDER")
    if forced:
        return forced
    if env.get("GITHUB_ACTIONS") == "true":
        return "github"
    if env.get("GITLAB_CI") == "true":
        return "gitlab"
    return "none"


def detect_context(
    env: Optional[Mapping[str, str]] = None,
    provider: str = "auto",
    api_url: Optional[str] = None,
    token_env: Optional[str] = None,
) -> CiContext:
    env = os.environ if env is None else env
    prov = detect_provider(env) if provider == "auto" else provider
    ctx = CiContext(provider=prov)

    if prov == "github":
        ctx.repo = env.get("VRT_REPO") or env.get("GITHUB_REPOSITORY", "")
        ctx.sha = env.get("VRT_SHA") or env.get("GITHUB_SHA", "")
        ctx.branch = env.get("GITHUB_HEAD_REF") or env.get("GITHUB_REF_NAME", "")
        ref = env.get("GITHUB_REF", "")
        m = re.match(r"refs/pull/(\d+)/", ref)
        ctx.pr = _int(env.get("VRT_PR")) or (int(m.group(1)) if m else None)
        # on pull_request events GITHUB_SHA is the *merge* commit; statuses belong on the PR head
        event_path = env.get("GITHUB_EVENT_PATH")
        if event_path and os.path.isfile(event_path) and not env.get("VRT_SHA"):
            try:
                ev = json.load(open(event_path))
                ctx.sha = ev.get("pull_request", {}).get("head", {}).get("sha") or ctx.sha
                ctx.pr = ctx.pr or ev.get("pull_request", {}).get("number")
            except (OSError, ValueError):
                pass
        ctx.api_url = api_url or env.get("VRT_CI_API_URL") or env.get("GITHUB_API_URL", "https://api.github.com")
        ctx.token = env.get(token_env or "GITHUB_TOKEN") or env.get("GITHUB_TOKEN")
        if env.get("GITHUB_RUN_ID") and ctx.repo:
            ctx.run_url = f"{env.get('GITHUB_SERVER_URL', 'https://github.com')}/{ctx.repo}/actions/runs/{env['GITHUB_RUN_ID']}"
    elif prov == "gitlab":
        ctx.repo = env.get("VRT_REPO") or env.get("CI_PROJECT_ID", "")
        ctx.sha = env.get("VRT_SHA") or env.get("CI_MERGE_REQUEST_SOURCE_BRANCH_SHA") or env.get("CI_COMMIT_SHA", "")
        ctx.branch = env.get("CI_COMMIT_REF_NAME", "")
        ctx.pr = _int(env.get("VRT_PR")) or _int(env.get("CI_MERGE_REQUEST_IID"))
        ctx.api_url = api_url or env.get("VRT_CI_API_URL") or env.get("CI_API_V4_URL", "https://gitlab.com/api/v4")
        ctx.token = env.get(token_env or "GITLAB_TOKEN") or env.get("GITLAB_TOKEN")
        ctx.run_url = env.get("CI_PIPELINE_URL")
    else:
        ctx.repo = env.get("VRT_REPO", "")
        ctx.sha = env.get("VRT_SHA", "")
        ctx.pr = _int(env.get("VRT_PR"))
        ctx.api_url = api_url or env.get("VRT_CI_API_URL", "")
        ctx.token = env.get(token_env) if token_env else None
    return ctx
