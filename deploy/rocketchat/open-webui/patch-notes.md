# Open WebUI App Integration — Exact Patch Checklist

This is the precise, no-guesswork list of edits to make in a target Open WebUI
(or Open WebUI-derived) codebase to wire in the Rocket.Chat integration.

Each entry is one of:
- **copy file** — drop the file in verbatim (see `backend-files.txt` / `frontend-files.txt`).
- **add import** — add an import line.
- **add block** — paste a code block at the indicated spot.
- **add route** — register a router.

All RC backend logic lives in the `[COPY]` files; the edits below are the thin
glue that activates them. Line numbers are approximate (they reference this
reference implementation) — match on the surrounding code, not the number.

---

## Backend

### 1. `backend/open_webui/env.py` — **add block**

At the end of the env definitions, add the entire `# Rocket.Chat Integration`
block (search for that header in this repo's `env.py`). It defines:

```python
ROCKETCHAT_ENABLED      # bool | None  — None = auto-detect
ROCKETCHAT_INTERNAL_URL # str          — container-to-container API URL
ROCKETCHAT_BASE_URL     # str          — browser-facing public URL
ROCKETCHAT_URL          # str          — legacy alias = ROCKETCHAT_INTERNAL_URL
ROCKETCHAT_ADMIN_USER, ROCKETCHAT_ADMIN_PASSWORD
ROCKETCHAT_SLASH_TOKEN, ROCKETCHAT_SLASH_MODEL
JITSI_URL, MATRIX_HOMESERVER_DOMAIN
RC_SYNC_QUEUE_DIR, VAPID_PUBLIC_KEY
```

### 2. `backend/open_webui/routers/__init__.py` / import site — **add import**

In `main.py`, where routers are imported:

```python
from open_webui.routers import (
    ...
    oauth_server,
    rocketchat_integrations,
    rocketchat_extras,
)
```

### 3. `backend/open_webui/main.py` — **add import** (env vars)

```python
from open_webui.env import (
    ...
    OAUTH_SERVER_CLIENT_ID,
    OAUTH_SERVER_CLIENT_SECRET,
    OAUTH_SERVER_REDIRECT_URIS,
    # Rocket.Chat Integration
    ROCKETCHAT_ENABLED,
    ROCKETCHAT_URL,
    ROCKETCHAT_INTERNAL_URL,
    ROCKETCHAT_BASE_URL,
    ROCKETCHAT_ADMIN_USER,
    ROCKETCHAT_ADMIN_PASSWORD,
    ROCKETCHAT_SLASH_TOKEN,
    ROCKETCHAT_SLASH_MODEL,
    VAPID_PUBLIC_KEY,
    JITSI_URL,
    MATRIX_HOMESERVER_DOMAIN,
)
```

### 4. `backend/open_webui/main.py` — **add block** (OAuth client registry)

Near app-state setup:

```python
app.state.oauth_server_clients = {}
if OAUTH_SERVER_CLIENT_ID and OAUTH_SERVER_CLIENT_SECRET:
    _redirect_uris = [u.strip() for u in OAUTH_SERVER_REDIRECT_URIS.split(',') if u.strip()]
    app.state.oauth_server_clients[OAUTH_SERVER_CLIENT_ID] = {
        'client_secret': OAUTH_SERVER_CLIENT_SECRET,
        'redirect_uris': _redirect_uris,
    }
```

### 5. `backend/open_webui/main.py` — **add block** (startup hooks)

Inside the lifespan startup, after DB init:

```python
from open_webui.utils import rocketchat as rc
_rc_should_init = (
    ROCKETCHAT_ENABLED is True
    or (ROCKETCHAT_ENABLED is None and bool(
        ROCKETCHAT_INTERNAL_URL and ROCKETCHAT_ADMIN_USER and ROCKETCHAT_ADMIN_PASSWORD
    ))
)
if _rc_should_init:
    rc.init(ROCKETCHAT_INTERNAL_URL, ROCKETCHAT_ADMIN_USER, ROCKETCHAT_ADMIN_PASSWORD)

from open_webui.utils import rocketchat_sync as _rc_sync_handlers  # noqa: F401 (registers queue handlers)
from open_webui.utils import rc_sync_queue
await rc_sync_queue.start()

from open_webui.utils.rocketchat_bridge import get_bridge
if rc.is_configured():
    asyncio.create_task(_start_bridge_with_retry())  # copy the retry helper too
```

Copy the `_start_bridge_with_retry()` coroutine from this repo's `main.py`
(8 attempts, exponential backoff 2s→60s).

### 6. `backend/open_webui/main.py` — **add route** (3 routers)

```python
app.include_router(oauth_server.router, tags=['oauth-server'])
app.include_router(rocketchat_integrations.router, tags=['rocketchat'])
app.include_router(rocketchat_extras.router, prefix='/api/v1', tags=['rocketchat-extras'])
```

### 7. `backend/open_webui/main.py` — **add block** (`/api/config` features)

In the `features` dict of `get_app_config`:

```python
from open_webui.utils import rocketchat as rc  # local import at top of the function
...
'rocketchat_enabled': rc.is_configured(),
'rocketchat_base_url': ROCKETCHAT_BASE_URL or None,
'rocketchat_slash_enabled': bool(ROCKETCHAT_SLASH_TOKEN),
'jitsi_url': JITSI_URL,
'web_push_vapid_public_key': VAPID_PUBLIC_KEY or None,
'matrix_homeserver_domain': MATRIX_HOMESERVER_DOMAIN,
```

### 8. `backend/open_webui/routers/auths.py` — **add import + block**

```python
from open_webui.utils import rocketchat_sync as rc_sync  # noqa: F401
from open_webui.utils import rc_sync_queue
```

After a successful signup/login profile update:

```python
await rc_sync_queue.enqueue('user.profile', {'user_id': user.id})
```

### 9. `backend/open_webui/routers/users.py` — **add import + blocks + endpoints**

```python
from open_webui.utils import rocketchat_sync as rc_sync  # noqa: F401
from open_webui.utils import rc_sync_queue
```

Enqueue on the relevant mutations:

```python
await rc_sync_queue.enqueue('user.role',    {'user_id': updated_user.id})       # role change
await rc_sync_queue.enqueue('user.profile', {'user_id': updated_user.id})       # profile/avatar change
await rc_sync_queue.enqueue('user.status',  {...})                              # status change
await rc_sync_queue.enqueue('user.delete',  {'user_id': ..., 'rc_user_id': ...})# delete
```

Also copy the admin RC endpoints from this repo's `users.py`:
`/{user_id}/rocketchat/active`, `/deactivate`, `/preferences`, `/push/register`,
and `/rocketchat/queue/*` (stats, jobs, replay-dead).

### 10. `backend/open_webui/routers/channels.py` — **add import + blocks**

```python
from open_webui.utils import rocketchat_sync as rc_sync          # noqa: F401
from open_webui.utils import rc_sync_queue
from open_webui.utils.rocketchat_bridge import get_bridge as _rc_bridge
```

Channel lifecycle:

```python
await rc_sync_queue.enqueue('dm.create',      {'channel_id': channel.id})   # DM create
await rc_sync_queue.enqueue('channel.create', {'channel_id': channel.id})   # channel create
await rc_sync_queue.enqueue('channel.update', {...})                        # update
await rc_sync_queue.enqueue('channel.delete', {'rc_room_id': ...})          # delete
```

Message parity (in the send/pin/edit/delete/react handlers):

```python
_rc_bridge().forward_to_rc(channel.id, message.content, message.id, files=attached_files)
# pin/edit/delete/react: see this repo's channels.py for the exact RC REST calls,
# which read rocketchat_message_id / rocketchat_room_id off message.data
```

Plus the channel-settings endpoints (`channel.topic`, `channel.announcement`,
`channel.read_only`, `channel.archive`, `channel.join_code`, `channel.default`,
`channel.role`, `channel.member`) and RC-augmented search — copy verbatim.

> Note: this implementation stores the RC message id back onto the OW message's
> `data` JSON column directly via SQLAlchemy in `rocketchat_bridge.py`. No new
> model method is required.

---

## Frontend

All RC UI is gated behind `$config?.features?.rocketchat_enabled` so it vanishes
cleanly when the integration is off.

### 11. `src/lib/apis/rocketchat.ts` — **copy file**

### 12. `src/lib/utils/push.ts` — **copy file**

### 13. `src/routes/(app)/admin/rocketchat/+page.svelte` — **copy file**

### 14. `src/routes/(app)/admin/+layout.svelte` — **add block**

Admin sidebar link:

```svelte
{#if $config?.features?.rocketchat_enabled}
  <a href="/admin/rocketchat" class="...">{$i18n.t('Rocket.Chat')}</a>
{/if}
```

### 15. `src/routes/+layout.svelte` — **add block**

Socket listener for live RC notifications:

```js
const rocketchatNotificationHandler = async (event) => { /* copy from repo */ };
$socket?.on('rocketchat:notification', rocketchatNotificationHandler);
// remember to $socket?.off(...) on cleanup
```

### 16. `src/lib/components/channel/MessageInput.svelte` — **add import + block**

```js
import { uploadAudioToRC, uploadFileToRC } from '$lib/apis/rocketchat';
```

Gate uploads on `$config?.features?.rocketchat_enabled && channel?.data?.rocketchat_room_id`.

### 17. Remaining components — **add block** (RC-gated UI)

`Messages/Message.svelte`, `Navbar.svelte`, `HighlightsModal.svelte`,
`URLPreview.svelte`, `admin/Users/UserList.svelte`, `chat/Settings/General.svelte`,
`routes/(app)/search/+page.svelte`, and `lib/apis/users/index.ts`
(`setRocketChatUserActive`). Diff each against this repo and copy the
`{#if $config?.features?.rocketchat_enabled}` blocks.

---

## Python dependencies

Ensure these are present in `backend/requirements.txt` (Open WebUI ships all
three already — verify):

```
httpx        # async REST client for the RC API
websockets   # DDP WebSocket transport
pyjwt        # id_token signing in oauth_server.py
```

---

## Done-when

- App boots with `ROCKETCHAT_ENABLED` unset and no RC env → no RC code runs.
- App boots with RC env set → `/api/config` reports `rocketchat_enabled: true`.
- Smoke test (`scripts/smoke-test.sh`) passes.
