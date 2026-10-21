"""CI/CD integration: status reporters (GitHub / GitLab / webhook), mock CI with branch protection, API service."""
from .context import CiContext, detect_context
from .render import comment_markdown, status_description, verdict_state
from .reporters import GitHubReporter, GitLabReporter, Reporter, WebhookReporter, make_reporters, publish_all, sign_payload

__all__ = [
    "CiContext",
    "GitHubReporter",
    "GitLabReporter",
    "Reporter",
    "WebhookReporter",
    "comment_markdown",
    "detect_context",
    "make_reporters",
    "publish_all",
    "sign_payload",
    "status_description",
    "verdict_state",
]
