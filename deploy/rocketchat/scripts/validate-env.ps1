# ===========================================================================
# Rocket.Chat integration - environment validator (Windows PowerShell)
# ===========================================================================
# Catches "looks configured but OAuth silently fails" before you deploy.
#
# Usage:
#   .\validate-env.ps1 [-EnvFile path\to\.env]
#
# Defaults to .\.env, then ..\.env. Exits non-zero if any required check fails.
# ===========================================================================
[CmdletBinding()]
param(
    [string]$EnvFile = ""
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

if ([string]::IsNullOrEmpty($EnvFile)) {
    foreach ($cand in @(".\.env", "..\.env", (Join-Path $scriptDir "..\.env"))) {
        if (Test-Path $cand) { $EnvFile = $cand; break }
    }
}

if ([string]::IsNullOrEmpty($EnvFile) -or -not (Test-Path $EnvFile)) {
    Write-Error "no .env file found. Pass one explicitly: .\validate-env.ps1 -EnvFile path\to\.env"
    exit 2
}

Write-Host "Validating: $EnvFile`n"

# --- load the env file -----------------------------------------------------
$env_map = @{}
foreach ($line in Get-Content $EnvFile) {
    $trimmed = $line.Trim()
    if ($trimmed -eq "" -or $trimmed.StartsWith("#")) { continue }
    $idx = $trimmed.IndexOf("=")
    if ($idx -lt 1) { continue }
    $key = $trimmed.Substring(0, $idx).Trim()
    $val = $trimmed.Substring($idx + 1)
    $val = $val.Trim().Trim('"').Trim("'")
    $env_map[$key] = $val
}

$script:errors = 0
$script:warnings = 0
function Fail($m) { Write-Host "  FAIL: $m" -ForegroundColor Red;    $script:errors++ }
function Warn($m) { Write-Host "  WARN: $m" -ForegroundColor Yellow; $script:warnings++ }
function Ok($m)   { Write-Host "  ok:   $m" -ForegroundColor Green }

function Get-Val($name) { if ($env_map.ContainsKey($name)) { return $env_map[$name] } return "" }
function Test-Url($u) { return ($u -match '^https?://') }
function Test-Placeholder($v) { return ($v -match 'change-me|example\.com|your-|REPLACE|placeholder') }

Write-Host "[1] Required variables present"
$required = @("WEBUI_URL","WEBUI_OAUTH_URL","ROCKETCHAT_PUBLIC_URL","ROCKETCHAT_INTERNAL_URL",
              "ROCKETCHAT_BASE_URL","OAUTH_SERVER_CLIENT_ID","OAUTH_SERVER_CLIENT_SECRET",
              "OAUTH_SERVER_REDIRECT_URIS","ROCKETCHAT_ADMIN_USER","ROCKETCHAT_ADMIN_PASSWORD",
              "ROCKETCHAT_ADMIN_EMAIL","WEBUI_SECRET_KEY","ROCKETCHAT_TAG")
foreach ($v in $required) {
    if ([string]::IsNullOrEmpty((Get-Val $v))) { Fail "$v is required but empty/missing" } else { Ok "$v set" }
}
Write-Host ""

Write-Host "[2] URLs use http/https"
foreach ($v in @("WEBUI_URL","WEBUI_OAUTH_URL","ROCKETCHAT_PUBLIC_URL","ROCKETCHAT_INTERNAL_URL","ROCKETCHAT_BASE_URL")) {
    $val = Get-Val $v
    if ([string]::IsNullOrEmpty($val)) { continue }
    if (Test-Url $val) { Ok "$v is a URL" } else { Fail "$v must start with http:// or https:// (got '$val')" }
}
Write-Host ""

Write-Host "[3] Redirect URI matches the Rocket.Chat OAuth callback"
$redirect = Get-Val "OAUTH_SERVER_REDIRECT_URIS"
$pub = Get-Val "ROCKETCHAT_PUBLIC_URL"
if ($redirect -match '/_oauth/openwebui$') { Ok "redirect URI ends with /_oauth/openwebui" }
else { Fail "OAUTH_SERVER_REDIRECT_URIS should end with /_oauth/openwebui (got '$redirect')" }
if ($redirect -and $pub) {
    try {
        $pubHost = ([Uri]$pub).Host
        $redHost = ([Uri]$redirect).Host
        if ($pubHost -eq $redHost) { Ok "redirect host matches ROCKETCHAT_PUBLIC_URL host ($pubHost)" }
        else { Fail "redirect host ($redHost) != ROCKETCHAT_PUBLIC_URL host ($pubHost)" }
    } catch { Warn "could not parse hosts for comparison" }
}
Write-Host ""

Write-Host "[4] ROCKETCHAT_BASE_URL matches ROCKETCHAT_PUBLIC_URL"
$base = Get-Val "ROCKETCHAT_BASE_URL"
if ($base -and $pub) {
    if ($base.TrimEnd('/') -eq $pub.TrimEnd('/')) { Ok "BASE_URL == PUBLIC_URL" }
    else { Warn "ROCKETCHAT_BASE_URL ($base) differs from ROCKETCHAT_PUBLIC_URL ($pub)" }
}
Write-Host ""

Write-Host "[5] Internal URL is container-reachable (not localhost)"
$internal = Get-Val "ROCKETCHAT_INTERNAL_URL"
if ($internal -match 'localhost|127\.0\.0\.1') {
    Warn "ROCKETCHAT_INTERNAL_URL points at localhost - inside Docker use http://rocketchat:3000"
} elseif ($internal) { Ok "internal URL is not localhost" }
Write-Host ""

Write-Host "[6] Secrets are not placeholders"
foreach ($v in @("OAUTH_SERVER_CLIENT_SECRET","ROCKETCHAT_ADMIN_PASSWORD","WEBUI_SECRET_KEY")) {
    $val = Get-Val $v
    if ([string]::IsNullOrEmpty($val)) { continue }
    if (Test-Placeholder $val) { Fail "$v still holds a placeholder value - generate a real secret" } else { Ok "$v is a real value" }
    if ($val.Length -lt 16) { Warn "$v is shorter than 16 chars - consider a longer secret" }
}
Write-Host ""

Write-Host "[7] Domains have been replaced"
foreach ($v in @("WEBUI_URL","ROCKETCHAT_PUBLIC_URL","ROCKETCHAT_BASE_URL","OAUTH_SERVER_REDIRECT_URIS")) {
    $val = Get-Val $v
    if ($val -match 'example\.com') { Warn "$v still references example.com - replace with your real domain" }
}
Write-Host ""

Write-Host "[8] Image tag is pinned (not :latest)"
$tag = Get-Val "ROCKETCHAT_TAG"
if ($tag -eq "latest" -or [string]::IsNullOrEmpty($tag)) { Fail "ROCKETCHAT_TAG must be a pinned version, not '$tag'" }
else { Ok "ROCKETCHAT_TAG=$tag" }
Write-Host ""

Write-Host "==========================================================="
Write-Host "Result: $script:errors error(s), $script:warnings warning(s)"
if ($script:errors -gt 0) {
    Write-Host "Environment is NOT ready. Fix the FAIL items above." -ForegroundColor Red
    exit 1
}
Write-Host "Environment looks valid. Review any warnings before deploying." -ForegroundColor Green
exit 0
