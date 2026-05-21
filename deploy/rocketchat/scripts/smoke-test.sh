#!/usr/bin/env bash
# ===========================================================================
# Rocket.Chat integration — post-deploy smoke test (Linux/macOS)
# ===========================================================================
# Runs against a RUNNING stack and proves the integration actually works end to
# end — the difference between "portable docs" and "portable package".
#
# Usage:
#   ./smoke-test.sh [path/to/.env]
#
# Requires: curl, and `docker` (for the container-to-container reachability and
# service-account login checks). Exits non-zero if any required check fails.
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
  echo "ERROR: no .env file found. Pass one explicitly: ./smoke-test.sh path/to/.env" >&2
  exit 2
fi

# --- load env --------------------------------------------------------------
declare -A ENV
while IFS= read -r line || [ -n "$line" ]; do
  case "$line" in ''|\#*) continue ;; esac
  key="${line%%=*}"; val="${line#*=}"
  key="$(echo "$key" | tr -d '[:space:]')"
  val="${val%\"}"; val="${val#\"}"; val="${val%\'}"; val="${val#\'}"
  ENV["$key"]="$val"
done < "$ENV_FILE"
get() { echo "${ENV[$1]:-}"; }

WEBUI_URL="$(get WEBUI_URL)"
RC_PUBLIC="$(get ROCKETCHAT_PUBLIC_URL)"
RC_INTERNAL="$(get ROCKETCHAT_INTERNAL_URL)"
RC_ADMIN_USER="$(get ROCKETCHAT_ADMIN_USER)"
RC_ADMIN_PASS="$(get ROCKETCHAT_ADMIN_PASSWORD)"
OW_SERVICE="${OPEN_WEBUI_SERVICE-open-webui}"

ERRORS=0
fail() { echo "  FAIL: $1" >&2; ERRORS=$((ERRORS+1)); }
ok()   { echo "  ok:   $1"; }

have_docker=0
if command -v docker >/dev/null 2>&1; then have_docker=1; fi

# helper: HTTP status of a URL
http_code() { curl -k -s -o /dev/null -w '%{http_code}' --max-time 15 "$1" 2>/dev/null; }

echo "Smoke test against:"
echo "  Open WebUI : $WEBUI_URL"
echo "  Rocket.Chat: $RC_PUBLIC"
echo

echo "[1] Open WebUI OIDC discovery reachable"
code="$(http_code "${WEBUI_URL%/}/.well-known/openid-configuration")"
if [ "$code" = "200" ]; then ok "/.well-known/openid-configuration -> 200"; else fail "OIDC discovery returned HTTP $code"; fi
echo

echo "[2] Rocket.Chat API reachable"
code="$(http_code "${RC_PUBLIC%/}/api/v1/info")"
if [ "$code" = "200" ]; then ok "/api/v1/info -> 200"; else fail "Rocket.Chat /api/v1/info returned HTTP $code"; fi
echo

echo "[3] OAuth authorize endpoint behaves (redirect or login)"
code="$(http_code "${WEBUI_URL%/}/oauth/authorize?client_id=$(get OAUTH_SERVER_CLIENT_ID)&redirect_uri=$(get OAUTH_SERVER_REDIRECT_URIS)&response_type=code&scope=openid")"
case "$code" in
  200|302|303|401|403) ok "/oauth/authorize -> $code (login/redirect behavior)" ;;
  *) fail "/oauth/authorize returned unexpected HTTP $code" ;;
esac
echo

echo "[4] /api/config exposes rocketchat_enabled = true"
cfg="$(curl -k -s --max-time 15 "${WEBUI_URL%/}/api/config" 2>/dev/null)"
if echo "$cfg" | grep -q '"rocketchat_enabled":[[:space:]]*true'; then
  ok "rocketchat_enabled is true"
else
  fail "rocketchat_enabled not true in /api/config (RC may be disabled or not yet initialised)"
fi
echo

echo "[5] Open WebUI backend can reach ROCKETCHAT_INTERNAL_URL"
if [ "$have_docker" = "1" ]; then
  if docker compose exec -T "$OW_SERVICE" \
       python -c "import sys,urllib.request; urllib.request.urlopen('${RC_INTERNAL%/}/api/v1/info', timeout=10); print('ok')" \
       >/dev/null 2>&1; then
    ok "container reached ${RC_INTERNAL%/}/api/v1/info"
  else
    fail "open-webui container could NOT reach ${RC_INTERNAL}/api/v1/info"
  fi
else
  echo "  skip: docker not available — cannot test container-to-container reachability"
fi
echo

echo "[6] Rocket.Chat service account can log in"
login_json="$(curl -k -s --max-time 15 -X POST "${RC_PUBLIC%/}/api/v1/login" \
  -d "user=${RC_ADMIN_USER}" -d "password=${RC_ADMIN_PASS}" 2>/dev/null)"
if echo "$login_json" | grep -q '"status":"success"'; then
  ok "service account '${RC_ADMIN_USER}' logged in"
  # be polite: log out the token we just created
  tok="$(echo "$login_json" | sed -n 's/.*"authToken":"\([^"]*\)".*/\1/p')"
  uid="$(echo "$login_json" | sed -n 's/.*"userId":"\([^"]*\)".*/\1/p')"
  if [ -n "$tok" ] && [ -n "$uid" ]; then
    curl -k -s --max-time 10 -X POST "${RC_PUBLIC%/}/api/v1/logout" \
      -H "X-Auth-Token: $tok" -H "X-User-Id: $uid" >/dev/null 2>&1
  fi
else
  fail "service account login failed — check ROCKETCHAT_ADMIN_USER/PASSWORD match on both sides"
fi
echo

echo "==========================================================="
echo "Result: $ERRORS error(s)"
if [ "$ERRORS" -gt 0 ]; then
  echo "Smoke test FAILED. The integration is not fully working."
  exit 1
fi
echo "Smoke test PASSED. The integration is working end to end."
exit 0
