#!/usr/bin/env bash
# M0 + M2 exit check against a running stack (docker compose up -d). Exits non-zero on any failure.
set -euo pipefail
BASE="${BASE_URL:-http://localhost:8080}"
fail() { echo "FAIL: $*" >&2; exit 1; }

echo "1/9 readiness"
curl -fsS "$BASE/readyz" | grep -q '"status":"ok"' || fail "/readyz not ok: $(curl -s "$BASE/readyz")"

echo "2/9 chat + embedding through the Ports"
out="$(docker compose exec -T api codura-docs llm ping)"
echo "$out" | grep -q "dimension 1024" || fail "embedding check failed: $out"
echo "$out" | grep -q "<think>" && fail "thinking output leaked: $out"

echo "3/9 job round trip"
job="$(curl -fsS -X POST "$BASE/api/admin/debug/jobs/noop" | sed -E 's/.*"job_id":"([^"]+)".*/\1/')"
for _ in $(seq 1 20); do
  status="$(curl -fsS "$BASE/api/admin/debug/jobs/$job")"
  echo "$status" | grep -q '"succeeded"' && break
  sleep 1
done
echo "$status" | grep -q '"succeeded"' || fail "job did not succeed: $status"

echo "4/9 email via Mailpit"
docker compose exec -T api codura-docs mail test smoke@example.test >/dev/null || fail "mail test"

echo "5/9 internal metrics not exposed"
[ "$(curl -s -o /dev/null -w '%{http_code}' "$BASE/metrics")" = "404" ] || fail "/metrics is public"

echo "6/9 web Channel assets (widget.js, hosted page, demo page)"
for path in /widget/v1/widget.js /widget/v1/hosted.js /c/demo/help /demo/; do
  [ "$(curl -s -o /dev/null -w '%{http_code}' "$BASE$path")" = "200" ] || fail "$path not served"
done

echo "7/9 Hosted Chat Page bootstrap (seeded /c/demo/help) and identical 404s"
curl -fsS "$BASE/api/v1/hosted/demo/help" | grep -q '"channel_key":"pk_' || fail "hosted bootstrap"
[ "$(curl -s -o /dev/null -w '%{http_code}' "$BASE/api/v1/hosted/demo/nope")" = "404" ] || fail "unknown hosted slug is not 404"

echo "8/9 Channel Key Origin check (SEC-05)"
KEY=pk_demo_widget_local_only_00000000000000000000000
ok="$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $KEY" -H 'X-Codura-Visitor: smoke-visitor-01' -H "Origin: $BASE" "$BASE/api/v1/channel/config")"
bad="$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $KEY" -H 'X-Codura-Visitor: smoke-visitor-01' -H 'Origin: https://evil.example' "$BASE/api/v1/channel/config")"
[ "$ok" = "200" ] && [ "$bad" = "403" ] || fail "origin check: allowed=$ok evil=$bad"

echo "9/9 billing: demo Tenant on Growth (Plan Entitlements reach the Channel)"
jar="$(mktemp)"
curl -fsS -c "$jar" -H 'Content-Type: application/json' -d '{"email":"admin@demo.test","password":"demo-password"}' "$BASE/api/admin/auth/login" > /dev/null || fail "demo login"
curl -fsS -b "$jar" "$BASE/api/admin/billing" | grep -q '"code":"growth"' || fail "demo billing overview"
curl -fsS -H "Authorization: Bearer $KEY" -H 'X-Codura-Visitor: smoke-visitor-01' -H "Origin: $BASE" "$BASE/api/v1/channel/config" | grep -q '"branding":false' || fail "remove_branding not applied"
rm -f "$jar"

echo "smoke: OK"
