# Rocket.Chat Integration — Migration Notes

This document is the reference for transplanting the Rocket.Chat integration
from this mock project into another Open WebUI deployment (e.g. NexBI).

---

## Overview

The integration turns Open WebUI into the identity provider (IdP) for
Rocket.Chat via OAuth 2.0 / OIDC, and keeps users, channels, messages, roles,
and presence in sync between the two systems in real time via a DDP WebSocket
bridge and a persistent job queue.

---

## Required Environment Variables

All variables are read from the OS environment (or Docker `.env` file).
None are hardcoded in source.

### Open WebUI service

| Variable | Required | Default | Description |
|---|---|---|---|
| `ROCKETCHAT_ENABLED` | No | auto | `true` to force-enable, `false` to force-disable. Omit to auto-detect from credentials. |
| `ROCKETCHAT_INTERNAL_URL` | Yes* | — | Container-to-container URL for backend→RC API calls. E.g. `http://rocketchat:3000`. *Required when auto-detecting. |
| `ROCKETCHAT_BASE_URL` | No | `ROCKETCHAT_INTERNAL_URL` | Browser-facing public URL for deep-links shown to users. E.g. `https://chat.example.com`. |
| `ROCKETCHAT_URL` | No | `ROCKETCHAT_INTERNAL_URL` | Legacy alias. Kept for backward compatibility. |
| `ROCKETCHAT_ADMIN_USER` | Yes* | — | Service-account username (admin bot, not a real user). |
| `ROCKETCHAT_ADMIN_PASSWORD` | Yes* | — | Service-account password. |
| `ROCKETCHAT_SLASH_TOKEN` | No | — | Shared secret for RC outgoing webhook `/ask` commands. |
| `ROCKETCHAT_SLASH_MODEL` | No | — | AI model to use for `/ask` slash command replies. |
| `OAUTH_SERVER_CLIENT_ID` | Yes | `rocketchat` | OAuth client ID RC uses to authenticate against Open WebUI. |
| `OAUTH_SERVER_CLIENT_SECRET` | Yes | — | OAuth client secret. Generate: `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `OAUTH_SERVER_REDIRECT_URIS` | Yes | — | Comma-separated list of allowed redirect URIs. Must include RC's callback URL. |
| `WEBUI_SECRET_KEY` | Yes | — | JWT signing key. Must be the same across all OW instances. |
| `WEBUI_URL` | Yes | `http://localhost:3000` | Public URL of Open WebUI. Appears in OIDC discovery document. |
| `JITSI_URL` | No | — | Jitsi server URL for video calls. E.g. `https://meet.jit.si`. |
| `MATRIX_HOMESERVER_DOMAIN` | No | — | RC Matrix federation domain. E.g. `chat.example.com`. |
| `VAPID_PUBLIC_KEY` | No | — | Web Push VAPID public key for browser push notifications. |
| `RC_SYNC_QUEUE_DIR` | No | `<DATA_DIR>/rc_sync_queue` | Directory for the persistent sync-job queue. |

### Rocket.Chat service

| Variable | Required | Default | Description |
|---|---|---|---|
| `ROCKETCHAT_ADMIN_USER` | Yes | `rc_admin` | Must match the OW `ROCKETCHAT_ADMIN_USER`. |
| `ROCKETCHAT_ADMIN_PASSWORD` | Yes | — | Must match the OW `ROCKETCHAT_ADMIN_PASSWORD`. |
| `ROCKETCHAT_ADMIN_EMAIL` | No | `rc-admin@example.com` | Admin email (must not collide with a real user). |
| `ROCKETCHAT_PUBLIC_URL` | No | `http://localhost:3100` | Browser-facing URL of RC (`ROOT_URL`). Use the same value as OW's `ROCKETCHAT_BASE_URL`. |
| `OAUTH_SERVER_CLIENT_ID` | Yes | `rocketchat` | Must match OW's `OAUTH_SERVER_CLIENT_ID`. |
| `OAUTH_SERVER_CLIENT_SECRET` | Yes | — | Must match OW's `OAUTH_SERVER_CLIENT_SECRET`. |
| `WEBUI_OAUTH_URL` | Yes | — | URL RC uses for OAuth token/userinfo calls. Must resolve from both browsers and inside the RC container. |

---

## Backend Files

All RC-specific backend code lives under `backend/open_webui/`:

```
utils/
  rocketchat.py          # RocketChatClient: REST API wrapper + init/is_configured
  rocketchat_sync.py     # Sync handlers: user/channel create/update/delete
  rocketchat_bridge.py   # DDP real-time bridge (WebSocket, OW→RC message forwarding)
  rc_sync_queue.py       # Persistent job queue with exponential backoff
  ddp_client.py          # Low-level DDP WebSocket protocol client

routers/
  rocketchat_extras.py   # /api/v1/rocketchat/* endpoints (messages, files, presence, audit, etc.)
  rocketchat_integrations.py  # /rocketchat/slash — outgoing webhook for /ask commands
  oauth_server.py        # OAuth 2.0 / OIDC server (/.well-known, /oauth/authorize, /oauth/token, /oauth/userinfo)
```

Files in other routers that contain small RC registration hooks (wrapping calls in `rc_sync_queue.enqueue`):

```
routers/auths.py         # Profile sync on login / signup
routers/users.py         # Role, profile, avatar, status, delete sync
routers/channels.py      # Channel create/update/delete sync; pin/edit/delete/react forwarding
```

### Router registration in `main.py`

```python
app.include_router(oauth_server.router, tags=['oauth-server'])
app.include_router(rocketchat_integrations.router, tags=['rocketchat'])
app.include_router(rocketchat_extras.router, prefix='/api/v1', tags=['rocketchat-extras'])
```

### Startup hooks in `main.py`

```python
# 1. Initialise the REST API client
from open_webui.utils import rocketchat as rc
rc.init(ROCKETCHAT_INTERNAL_URL, ROCKETCHAT_ADMIN_USER, ROCKETCHAT_ADMIN_PASSWORD)

# 2. Start the persistent sync queue (registers handlers from rocketchat_sync)
from open_webui.utils import rocketchat_sync as _rc_sync_handlers  # registers handlers
from open_webui.utils import rc_sync_queue
await rc_sync_queue.start()

# 3. Start the DDP real-time bridge (with retry/backoff)
from open_webui.utils.rocketchat_bridge import get_bridge
if rc.is_configured():
    asyncio.create_task(_start_bridge_with_retry())
```

---

## Frontend Files

RC-specific frontend files:

```
src/lib/apis/rocketchat.ts                          # API helpers for RC admin endpoints
src/lib/utils/push.ts                               # Web Push subscription + RC notification routing
src/routes/(app)/admin/rocketchat/+page.svelte      # Admin → Rocket.Chat settings page
```

Frontend components that conditionally render RC features (guarded by `$config.features.rocketchat_enabled`):

```
src/lib/components/channel/MessageInput.svelte      # File upload to RC, audio recording
src/lib/components/channel/Messages/Message.svelte  # URL preview via RC
src/lib/components/channel/Navbar.svelte            # RC open button, highlights
src/lib/components/channel/HighlightsModal.svelte   # RC message highlights
src/lib/components/channel/URLPreview.svelte        # URL preview card
src/lib/components/admin/Users/UserList.svelte      # "Open in RC" link per user
src/lib/components/chat/Settings/General.svelte     # Browser notifications with RC
src/routes/(app)/admin/+layout.svelte               # Admin sidebar "Rocket.Chat" link
src/routes/(app)/search/+page.svelte                # RC-augmented search results
src/routes/+layout.svelte                           # RC push notification listener
```

### Frontend config access

The frontend reads RC config from `/api/config` response:

```js
$config.features.rocketchat_enabled   // boolean — gate all RC UI
$config.features.rocketchat_base_url  // string | null — public RC URL for deep-links
$config.features.rocketchat_slash_enabled  // boolean — /ask slash command
```

---

## Docker / Service Requirements

- **MongoDB 7.0+** with replica set (`rs0`) — required by Rocket.Chat.
- **Rocket.Chat** (any recent version) — configured via `OVERWRITE_SETTING_*` env vars to use Open WebUI as OAuth IdP.
- **Open WebUI** must be reachable at the same URL from both browsers and from inside the Rocket.Chat container (`WEBUI_OAUTH_URL`).

Healthcheck for Rocket.Chat (use `condition: service_healthy` in `depends_on`):

```yaml
healthcheck:
  test: ["CMD-SHELL", "wget -qO- http://localhost:3000/api/v1/info || exit 1"]
  interval: 15s
  timeout: 10s
  retries: 12
  start_period: 90s
```

Fail-fast secrets (use `:?` syntax in `docker-compose.yaml`):

```yaml
WEBUI_SECRET_KEY=${WEBUI_SECRET_KEY:?WEBUI_SECRET_KEY must be set}
OAUTH_SERVER_CLIENT_SECRET=${OAUTH_SERVER_CLIENT_SECRET:?...}
ROCKETCHAT_ADMIN_PASSWORD=${ROCKETCHAT_ADMIN_PASSWORD:?...}
```

---

## Backend API Routes Exposed

| Method | Path | Description |
|---|---|---|
| `GET` | `/.well-known/openid-configuration` | OIDC discovery document |
| `GET` | `/oauth/authorize` | OAuth 2.0 authorization endpoint |
| `POST` | `/oauth/token` | OAuth 2.0 token endpoint |
| `GET` | `/oauth/userinfo` | OIDC userinfo endpoint (also triggers RC user provisioning) |
| `POST` | `/rocketchat/slash` | Outgoing webhook handler for RC `/ask` AI command |
| `GET/POST/DELETE` | `/api/v1/rocketchat/messages/*` | Message operations proxied to RC |
| `POST` | `/api/v1/rocketchat/files/*` | File upload to RC rooms |
| `GET/POST` | `/api/v1/rocketchat/presence/*` | User presence sync |
| `GET` | `/api/v1/rocketchat/audit/*` | RC audit log viewer |
| `GET` | `/api/v1/rocketchat/webhooks/*` | RC webhook management |
| `GET` | `/api/v1/rocketchat/apps/*` | RC marketplace app list |
| `GET` | `/api/v1/rocketchat/bot/*` | RC bot/omnichannel integration |
| `GET` | `/api/v1/search` | Unified search (OW + RC) |
| `GET/POST` | `/api/v1/teams/*` | RC team management |

---

## Step-by-Step Migration Checklist

Follow these steps to copy the Rocket.Chat integration into the NexBI/Open WebUI deployment project.

### 1 — Copy backend files

Copy these files verbatim into the target project's `backend/open_webui/` tree:

```
utils/rocketchat.py
utils/rocketchat_sync.py
utils/rocketchat_bridge.py
utils/rc_sync_queue.py
utils/ddp_client.py
routers/rocketchat_extras.py
routers/rocketchat_integrations.py
routers/oauth_server.py
```

### 2 — Copy frontend files

Copy these files verbatim:

```
src/lib/apis/rocketchat.ts
src/lib/utils/push.ts
src/routes/(app)/admin/rocketchat/+page.svelte
```

### 3 — Merge env vars into `env.py`

Add the Rocket.Chat block from this project's `backend/open_webui/env.py`
(search for `# Rocket.Chat Integration`) into the target's `env.py`.

New variables added for portability:

```python
ROCKETCHAT_ENABLED: bool | None   # None = auto-detect
ROCKETCHAT_INTERNAL_URL: str      # container-to-container URL
ROCKETCHAT_BASE_URL: str          # browser-facing public URL
ROCKETCHAT_URL: str               # legacy alias = ROCKETCHAT_INTERNAL_URL
```

### 4 — Register routers in `main.py`

Add to the router registration block:

```python
from open_webui.routers import oauth_server, rocketchat_integrations, rocketchat_extras
app.include_router(oauth_server.router, tags=['oauth-server'])
app.include_router(rocketchat_integrations.router, tags=['rocketchat'])
app.include_router(rocketchat_extras.router, prefix='/api/v1', tags=['rocketchat-extras'])
```

### 5 — Add startup hooks in `main.py`

Inside the `lifespan` startup block, after database initialisation:

```python
from open_webui.env import (
    ROCKETCHAT_ENABLED, ROCKETCHAT_INTERNAL_URL,
    ROCKETCHAT_BASE_URL, ROCKETCHAT_ADMIN_USER, ROCKETCHAT_ADMIN_PASSWORD,
)
from open_webui.utils import rocketchat as rc

_rc_should_init = (
    ROCKETCHAT_ENABLED is True
    or (ROCKETCHAT_ENABLED is None and bool(
        ROCKETCHAT_INTERNAL_URL and ROCKETCHAT_ADMIN_USER and ROCKETCHAT_ADMIN_PASSWORD
    ))
)
if _rc_should_init:
    rc.init(ROCKETCHAT_INTERNAL_URL, ROCKETCHAT_ADMIN_USER, ROCKETCHAT_ADMIN_PASSWORD)

from open_webui.utils import rocketchat_sync as _rc_sync_handlers  # noqa: F401
from open_webui.utils import rc_sync_queue
await rc_sync_queue.start()

from open_webui.utils.rocketchat_bridge import get_bridge
if rc.is_configured():
    asyncio.create_task(_start_bridge_with_retry())
```

### 6 — Expose RC config to frontend via `/api/config`

In the `/api/config` endpoint's `features` dict:

```python
from open_webui.utils import rocketchat as rc
...
'rocketchat_enabled': rc.is_configured(),
'rocketchat_base_url': ROCKETCHAT_BASE_URL or None,
'rocketchat_slash_enabled': bool(ROCKETCHAT_SLASH_TOKEN),
```

### 7 — Add small sync hooks to existing routers

Each hook is a single `rc_sync_queue.enqueue(...)` call guarded by `rc.is_configured()`:

- `routers/auths.py` — enqueue `user.profile` on signup/login
- `routers/users.py` — enqueue `user.role`, `user.profile`, `user.avatar`, `user.status`, `user.delete`
- `routers/channels.py` — enqueue `channel.create`, `channel.update`, `channel.delete`; call bridge for pin/edit/delete/react

Grep for `rc_sync_queue.enqueue` in the source files for the exact call sites.

### 8 — Add Docker services

Add the `mongodb` and `rocketchat` service blocks from `docker-compose.yaml` to the target compose file.

Set environment variables for the target deployment (see "Required Environment Variables" above).

Key decision: set `WEBUI_OAUTH_URL` to a URL that resolves identically from both browsers **and** from inside the Rocket.Chat container.

### 9 — Install Python dependencies

The integration requires:

```
httpx          # async HTTP client for RC REST API
websockets     # DDP WebSocket client
pyjwt          # id_token generation in oauth_server.py
```

These are almost certainly already in Open WebUI's `requirements.txt`. Verify with:

```bash
pip show httpx websockets pyjwt
```

### 10 — First-run verification

1. Start the stack: `docker compose up -d`
2. Wait for all services to be healthy: `docker compose ps`
3. Open Open WebUI in a browser; create or log in as a user.
4. Open Rocket.Chat at `ROCKETCHAT_BASE_URL`; sign in with "Sign in with Open WebUI".
5. Confirm the user appears in both systems.
6. Send a message in an OW channel; confirm it appears in the matching RC room.

---

## What Was Made Portable

| Change | File | Detail |
|---|---|---|
| Added `ROCKETCHAT_ENABLED` feature flag | `env.py` | Explicit on/off control independent of credentials |
| Added `ROCKETCHAT_INTERNAL_URL` | `env.py` | Explicit container-internal URL; `ROCKETCHAT_URL` becomes a legacy alias |
| Added `ROCKETCHAT_BASE_URL` | `env.py` | Browser-facing public URL, distinct from internal URL |
| Updated startup to use `ROCKETCHAT_INTERNAL_URL` | `main.py` | No hardcoded hostnames |
| Updated `_start_bridge_with_retry` | `main.py` | Uses `ROCKETCHAT_INTERNAL_URL` |
| Expose `rocketchat_base_url` in `/api/config` | `main.py` | Frontend can generate correct deep-links |
| Updated `docker-compose.yaml` | `docker-compose.yaml` | All three new vars wired up; legacy `ROCKETCHAT_URL` preserved |
| This file | `MIGRATION_NOTES.md` | Step-by-step migration checklist |

No existing functionality was removed or broken.

---

## Running the Mock After Refactor

The existing `docker-compose.yaml` workflow is unchanged:

```bash
# 1. Create a .env file with required secrets
cp .env.example .env   # if one exists, otherwise create manually
# Set at minimum:
#   WEBUI_SECRET_KEY=<random 32-byte hex>
#   OAUTH_SERVER_CLIENT_SECRET=<random>
#   ROCKETCHAT_ADMIN_PASSWORD=<password>
#   WEBUI_OAUTH_URL=http://host.docker.internal:3000

# 2. Start everything
docker compose up -d

# 3. Tail logs
docker compose logs -f open-webui rocketchat
```

To disable RC while keeping the stack running:

```bash
ROCKETCHAT_ENABLED=false docker compose up -d open-webui
```
