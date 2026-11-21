#!/usr/bin/env bash
# M0 exit check against a running stack (docker compose up -d). Exits non-zero on any failure.
set -euo pipefail
BASE="${BASE_URL:-http://localhost:8080}"
fail() { echo "FAIL: $*" >&2; exit 1; }

echo "1/5 readiness"
curl -fsS "$BASE/readyz" | grep -q '"status":"ok"' || fail "/readyz not ok: $(curl -s "$BASE/readyz")"

echo "2/5 chat + embedding through the Ports"
out="$(docker compose exec -T api codura-docs llm ping)"
echo "$out" | grep -q "dimension 1024" || fail "embedding check failed: $out"
echo "$out" | grep -q "<think>" && fail "thinking output leaked: $out"

echo "3/5 job round trip"
job="$(curl -fsS -X POST "$BASE/api/admin/debug/jobs/noop" | sed -E 's/.*"job_id":"([^"]+)".*/\1/')"
for _ in $(seq 1 20); do
  status="$(curl -fsS "$BASE/api/admin/debug/jobs/$job")"
  echo "$status" | grep -q '"succeeded"' && break
  sleep 1
done
echo "$status" | grep -q '"succeeded"' || fail "job did not succeed: $status"

echo "4/5 email via Mailpit"
docker compose exec -T api codura-docs mail test smoke@example.test >/dev/null || fail "mail test"

echo "5/5 internal metrics not exposed"
[ "$(curl -s -o /dev/null -w '%{http_code}' "$BASE/metrics")" = "404" ] || fail "/metrics is public"

echo "smoke: OK"
