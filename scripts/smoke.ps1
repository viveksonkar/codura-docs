# M0 exit check against a running stack (docker compose up -d). Windows PowerShell 5.1+.
$ErrorActionPreference = "Stop"
$Base = if ($env:BASE_URL) { $env:BASE_URL } else { "http://localhost:8080" }
function Fail($msg) { Write-Error "FAIL: $msg"; exit 1 }

Write-Host "1/5 readiness"
$ready = Invoke-RestMethod "$Base/readyz"
if ($ready.status -ne "ok") { Fail "/readyz not ok: $($ready | ConvertTo-Json -Compress)" }

Write-Host "2/5 chat + embedding through the Ports"
$out = (docker compose exec -T api codura-docs llm ping) -join "`n"
if ($out -notmatch "dimension 1024") { Fail "embedding check failed: $out" }
if ($out -match "<think>") { Fail "thinking output leaked: $out" }

Write-Host "3/5 job round trip"
$job = (Invoke-RestMethod -Method Post "$Base/api/admin/debug/jobs/noop").job_id
$status = $null
foreach ($i in 1..20) {
  $status = Invoke-RestMethod "$Base/api/admin/debug/jobs/$job"
  if ($status.status -eq "succeeded") { break }
  Start-Sleep -Seconds 1
}
if ($status.status -ne "succeeded") { Fail "job did not succeed: $($status | ConvertTo-Json -Compress)" }

Write-Host "4/5 email via Mailpit"
docker compose exec -T api codura-docs mail test smoke@example.test | Out-Null
if ($LASTEXITCODE -ne 0) { Fail "mail test" }

Write-Host "5/5 internal metrics not exposed"
$code = curl.exe -s -o NUL -w "%{http_code}" "$Base/metrics"
if ($code -ne "404") { Fail "/metrics is public ($code)" }

Write-Host "smoke: OK"
