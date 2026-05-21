#!/usr/bin/env bash
# ===========================================================================
# Rocket.Chat integration — environment validator (Linux/macOS)
# ===========================================================================
# Catches "looks configured but OAuth silently fails" before you deploy.
#
# Usage:
#   ./validate-env.sh [path/to/.env]
#
# Defaults to ./.env, then ../.env, then the package .env.example as a fallback
# reference. Exits non-zero if any required check fails.
# ===========================================================================
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${1:-}"

if [ -z "$ENV_FILE" ]; then
  for cand in "./.env" "../.env" "${SCRIPT_DIR}/../.env"; do
    if [ -f "$cand" ]; then ENV_FILE="$cand"; break; fi
  done
fi

if [ -z "$ENV_FILE" ] || [ ! -f "$ENV_FILE" ]; then
  echo "ERROR: no .env file found. Pass one explicitly: ./validate-env.sh path/to/.env" >&2
  exit 2
fi

echo "Validating: $ENV_FILE"
echo

# --- load the env file without executing it -------------------------------
declare -A ENV
while IFS= read -r line || [ -n "$line" ]; do
  case "$line" in
    ''|\#*) continue ;;
  esac
  key="${line%%=*}"
  val="${line#*=}"
  key="$(echo "$key" | tr -d '[:space:]')"
  # strip surrounding quotes
  val="${val%\"}"; val="${val#\"}"
  val="${val%\'}"; val="${val#\'}"
  ENV["$key"]="$val"
done < "$ENV_FILE"

ERRORS=0
WARNINGS=0
fail() { echo "  FAIL: $1" >&2; ERRORS=$((ERRORS+1)); }
warn() { echo "  WARN: $1" >&2; WARNINGS=$((WARNINGS+1)); }
ok()   { echo "  ok:   $1"; }

get() { echo "${ENV[$1]:-}"; }

require() {
  local name="$1"; local v; v="$(get "$name")"
  if [ -z "$v" ]; then fail "$name is required but empty/missing"; return 1; fi
  return 0
}

is_url() {
  case "$1" in
    http://*|https://*) return 0 ;;
    *) return 1 ;;
  esac
}

is_placeholder() {
  case "$1" in
    *change-me*|*example.com*|*your-*|*REPLACE*|*placeholder*) return 0 ;;
    *) return 1 ;;
  esac
}

echo "[1] Required variables present"
for v in WEBUI_URL WEBUI_OAUTH_URL ROCKETCHAT_PUBLIC_URL ROCKETCHAT_INTERNAL_URL \
         ROCKETCHAT_BASE_URL OAUTH_SERVER_CLIENT_ID OAUTH_SERVER_CLIENT_SECRET \
         OAUTH_SERVER_REDIRECT_URIS ROCKETCHAT_ADMIN_USER ROCKETCHAT_ADMIN_PASSWORD \
         ROCKETCHAT_ADMIN_EMAIL WEBUI_SECRET_KEY ROCKETCHAT_TAG; do
  if require "$v"; then ok "$v set"; fi
done
echo

echo "[2] URLs use http/https"
for v in WEBUI_URL WEBUI_OAUTH_URL ROCKETCHAT_PUBLIC_URL ROCKETCHAT_INTERNAL_URL ROCKETCHAT_BASE_URL; do
  val="$(get "$v")"
  [ -z "$val" ] && continue
  if is_url "$val"; then ok "$v is a URL"; else fail "$v must start with http:// or https:// (got '$val')"; fi
done
echo

echo "[3] Redirect URI matches the Rocket.Chat OAuth callback"
redirect="$(get OAUTH_SERVER_REDIRECT_URIS)"
pub="$(get ROCKETCHAT_PUBLIC_URL)"
case "$redirect" in
  */_oauth/openwebui) ok "redirect URI ends with /_oauth/openwebui" ;;
  *) fail "OAUTH_SERVER_REDIRECT_URIS should end with /_oauth/openwebui (got '$redirect')" ;;
esac
if [ -n "$pub" ] && [ -n "$redirect" ]; then
  pub_host="${pub#*://}"; pub_host="${pub_host%%/*}"
  red_host="${redirect#*://}"; red_host="${red_host%%/*}"
  if [ "$pub_host" = "$red_host" ]; then
    ok "redirect host matches ROCKETCHAT_PUBLIC_URL host ($pub_host)"
  else
    fail "redirect host ($red_host) != ROCKETCHAT_PUBLIC_URL host ($pub_host)"
  fi
fi
echo

echo "[4] ROCKETCHAT_BASE_URL matches ROCKETCHAT_PUBLIC_URL"
base="$(get ROCKETCHAT_BASE_URL)"
if [ -n "$base" ] && [ -n "$pub" ]; then
  if [ "${base%/}" = "${pub%/}" ]; then ok "BASE_URL == PUBLIC_URL"; else
    warn "ROCKETCHAT_BASE_URL ($base) differs from ROCKETCHAT_PUBLIC_URL ($pub) — only correct if RC is reached via two distinct browser URLs"
  fi
fi
echo

echo "[5] Internal URL is container-reachable (not localhost)"
internal="$(get ROCKETCHAT_INTERNAL_URL)"
case "$internal" in
  *localhost*|*127.0.0.1*) warn "ROCKETCHAT_INTERNAL_URL points at localhost — inside Docker this won't reach the rocketchat container; use http://rocketchat:3000" ;;
  *) [ -n "$internal" ] && ok "internal URL is not localhost" ;;
esac
echo

echo "[6] Secrets are not placeholders"
for v in OAUTH_SERVER_CLIENT_SECRET ROCKETCHAT_ADMIN_PASSWORD WEBUI_SECRET_KEY; do
  val="$(get "$v")"
  [ -z "$val" ] && continue
  if is_placeholder "$val"; then fail "$v still holds a placeholder value — generate a real secret"; else ok "$v is a real value"; fi
  if [ "${#val}" -lt 16 ]; then warn "$v is shorter than 16 chars — consider a longer secret"; fi
done
echo

echo "[7] Domains have been replaced"
for v in WEBUI_URL ROCKETCHAT_PUBLIC_URL ROCKETCHAT_BASE_URL OAUTH_SERVER_REDIRECT_URIS; do
  val="$(get "$v")"
  case "$val" in *example.com*) warn "$v still references example.com — replace with your real domain" ;; esac
done
echo

echo "[8] Image tag is pinned (not :latest)"
tag="$(get ROCKETCHAT_TAG)"
case "$tag" in
  latest|"" ) fail "ROCKETCHAT_TAG must be a pinned version, not '$tag'" ;;
  *) ok "ROCKETCHAT_TAG=$tag" ;;
esac
echo

echo "==========================================================="
echo "Result: $ERRORS error(s), $WARNINGS warning(s)"
if [ "$ERRORS" -gt 0 ]; then
  echo "Environment is NOT ready. Fix the FAIL items above."
  exit 1
fi
echo "Environment looks valid. Review any warnings before deploying."
exit 0
