# M0 + M2 exit check against a running stack (docker compose up -d). Windows PowerShell 5.1+.
$ErrorActionPreference = "Stop"
$Base = if ($env:BASE_URL) { $env:BASE_URL } else { "http://localhost:8080" }
function Fail($msg) { Write-Error "FAIL: $msg"; exit 1 }

Write-Host "1/8 readiness"
$ready = Invoke-RestMethod "$Base/readyz"
if ($ready.status -ne "ok") { Fail "/readyz not ok: $($ready | ConvertTo-Json -Compress)" }

Write-Host "2/8 chat + embedding through the Ports"
$out = (docker compose exec -T api codura-docs llm ping) -join "`n"
if ($out -notmatch "dimension 1024") { Fail "embedding check failed: $out" }
if ($out -match "<think>") { Fail "thinking output leaked: $out" }

Write-Host "3/8 job round trip"
$job = (Invoke-RestMethod -Method Post "$Base/api/admin/debug/jobs/noop").job_id
$status = $null
foreach ($i in 1..20) {
  $status = Invoke-RestMethod "$Base/api/admin/debug/jobs/$job"
  if ($status.status -eq "succeeded") { break }
  Start-Sleep -Seconds 1
}
if ($status.status -ne "succeeded") { Fail "job did not succeed: $($status | ConvertTo-Json -Compress)" }

Write-Host "4/8 email via Mailpit"
docker compose exec -T api codura-docs mail test smoke@example.test | Out-Null
if ($LASTEXITCODE -ne 0) { Fail "mail test" }

Write-Host "5/8 internal metrics not exposed"
$code = curl.exe -s -o NUL -w "%{http_code}" "$Base/metrics"
if ($code -ne "404") { Fail "/metrics is public ($code)" }

Write-Host "6/8 web Channel assets (widget.js, hosted page, demo page)"
foreach ($path in "/widget/v1/widget.js", "/widget/v1/hosted.js", "/c/demo/help", "/demo/") {
  $code = curl.exe -s -o NUL -w "%{http_code}" "$Base$path"
  if ($code -ne "200") { Fail "$path not served ($code)" }
}

Write-Host "7/8 Hosted Chat Page bootstrap (seeded /c/demo/help) and identical 404s"
$hosted = Invoke-RestMethod "$Base/api/v1/hosted/demo/help"
if (-not $hosted.channel_key.StartsWith("pk_")) { Fail "hosted bootstrap" }
$code = curl.exe -s -o NUL -w "%{http_code}" "$Base/api/v1/hosted/demo/nope"
if ($code -ne "404") { Fail "unknown hosted slug is not 404 ($code)" }

Write-Host "8/8 Channel Key Origin check (SEC-05)"
$key = "pk_demo_widget_local_only_00000000000000000000000"
$ok = curl.exe -s -o NUL -w "%{http_code}" -H "Authorization: Bearer $key" -H "X-Codura-Visitor: smoke-visitor-01" -H "Origin: $Base" "$Base/api/v1/channel/config"
$bad = curl.exe -s -o NUL -w "%{http_code}" -H "Authorization: Bearer $key" -H "X-Codura-Visitor: smoke-visitor-01" -H "Origin: https://evil.example" "$Base/api/v1/channel/config"
if ($ok -ne "200" -or $bad -ne "403") { Fail "origin check: allowed=$ok evil=$bad" }

Write-Host "smoke: OK"
