# CI integration

## GitHub Actions
`.github/workflows/vrt.yml` runs `vrt test` on every pull request. With `permissions: statuses, checks, pull-requests: write` the run publishes

* commit status `vrt/visual-regression` (`success` / `failure` / `error`),
* a check run with the per-page table,
* one PR comment, updated on every push (no comment spam),
* the HTML report and the failing captures as workflow artifacts.

Mark `vrt/visual-regression` as a **required status check** in branch protection and a visual regression blocks the merge.
Baselines live in the repository (`baseline_dir`), so changing the look of the product is a normal reviewed diff of PNG files.
To accept an intended change: run `vrt approve -c <config>` locally (or download the `vrt-candidates` artifact), commit the new baselines.

## GitLab
`ci/gitlab-ci.example.yml`. Needs a project access token (`api` scope) in `GITLAB_TOKEN`; the job token cannot set commit statuses.
Reports are published as commit status + merge-request note; `junit.xml` appears in the pipeline's Tests tab.

## Webhook and API
`vrt serve` starts the FastAPI service (`/healthz`, `/metrics`, `/api/v1/compare`, `/api/v1/runs`, `/api/v1/reports/…`, `/webhook`).
Webhook calls must carry `X-Hub-Signature-256: sha256=HMAC(secret, body)`; without `VRT_WEBHOOK_SECRET` they are rejected unless
`VRT_ALLOW_UNSIGNED_WEBHOOKS=1` (development only).

```bash
curl -F baseline=@a.png -F candidate=@b.png -F report=true http://localhost:8000/api/v1/compare
```

## Offline check of the gate
`vrt mock-ci` is a small GitHub/GitLab API double with a required-status merge gate. `scripts/demo.sh` and `tests/test_ci.py` use it:
red status → `PUT /pulls/1/merge` returns **405**; green → **200**.

## Choosing thresholds in production
* Start with profile `balanced` and a pinned render environment (the Docker image). Pinning removes more false positives than any tolerance does.
* Mask what is genuinely dynamic (`mask_selectors`) or let `vrt baseline --stability-runs 3` find it.
* If a team's review queue fills with false alarms, raise `decision.fail_pixels` first (smooth FP/FN trade-off); change `color.delta_e` last.
* Re-run `make calibrate` on screenshots of *your* product before trusting the numbers in `results/`.
