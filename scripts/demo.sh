#!/usr/bin/env bash
# End-to-end demo without Docker: baseline -> regression found -> CI gate blocks the merge -> approve -> merge allowed.
set -uo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH=.
SITE_PORT=${SITE_PORT:-8080}
CI_PORT=${CI_PORT:-9100}
CFG=examples/vrt.config.yaml
VRT="python3 -m core"
SHA=0123456789abcdef0123456789abcdef01234567

rm -rf .vrt-demo
python3 -m http.server "$SITE_PORT" --directory examples/demo-site >/dev/null 2>&1 & SITE=$!
$VRT mock-ci --port "$CI_PORT" >/dev/null 2>&1 & MOCK=$!
trap 'kill $SITE $MOCK 2>/dev/null' EXIT
for i in $(seq 1 50); do curl -fs "http://127.0.0.1:$SITE_PORT/v1/index.html" >/dev/null && curl -fs "http://127.0.0.1:$CI_PORT/healthz" >/dev/null && break; sleep 0.2; done

CI="http://127.0.0.1:$CI_PORT"
export VRT_CI_PROVIDER=github VRT_CI_API_URL=$CI VRT_REPO=acme/site VRT_SHA=$SHA VRT_PR=1 GITHUB_TOKEN=demo
curl -fs -X PUT "$CI/__protection" -H 'content-type: application/json' -d '{"contexts":["vrt/visual-regression"]}' >/dev/null
curl -fs -X POST "$CI/repos/acme/site/pulls" -H 'content-type: application/json' -H 'authorization: Bearer demo' -d "{\"head\":\"$SHA\",\"title\":\"Restyle\"}" >/dev/null

step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
merge() { curl -s -o /dev/null -w '%{http_code}' -X PUT "$CI/repos/acme/site/pulls/1/merge" -H 'authorization: Bearer demo'; }

step "1. baselines from v1"
VRT_BASE_URL="http://127.0.0.1:$SITE_PORT/v1" $VRT baseline -c $CFG --stability-runs 2

step "2. test the unchanged site (v1) -> expect exit 0"
VRT_BASE_URL="http://127.0.0.1:$SITE_PORT/v1" $VRT test -c $CFG --no-ci; echo "exit code: $?"

step "3. test v2 (deliberate regressions) with CI reporting -> expect exit 1"
VRT_BASE_URL="http://127.0.0.1:$SITE_PORT/v2" $VRT test -c $CFG; echo "exit code: $?"
echo "merge attempt -> HTTP $(merge)  (405 = blocked by the required status check)"

step "4. approve the new look and test again -> expect exit 0 and merge allowed"
$VRT approve -c $CFG
VRT_BASE_URL="http://127.0.0.1:$SITE_PORT/v2" $VRT test -c $CFG; echo "exit code: $?"
echo "merge attempt -> HTTP $(merge)  (200 = allowed)"

echo; echo "HTML report: .vrt-demo/report/index.html"
