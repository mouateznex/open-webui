# Migration Checklist — Transplant into NexBI / Open WebUI

A linear checklist for copying this Rocket.Chat integration into the target
project (`nexbi/deployment` or any Open WebUI-derived app). Tick each box.

The target project uses isolated integrations (like its JupyterLab setup):
dedicated route namespace, env-driven config, optional Docker layer, no
hardcoded local URLs. This package matches that style.

---

## Phase 0 — Prerequisites

- [ ] Target project is a working Open WebUI (or fork) that already builds/runs.
- [ ] Target has Python deps `httpx`, `websockets`, `pyjwt` (Open WebUI ships them).
- [ ] You have a Rocket.Chat version chosen and pinned (`ROCKETCHAT_TAG`).
- [ ] You know the final public domains for Open WebUI and Rocket.Chat.

## Phase 1 — Copy the deployment package

- [ ] Copy the entire `deploy/rocketchat/` directory into the target repo root.
- [ ] Confirm `deploy/rocketchat/compose.yaml`, `compose.open-webui.override.yaml`,
      `.env.example`, `scripts/`, and `open-webui/` came across.

## Phase 2 — Copy backend code

The source files live in `deploy/rocketchat/open-webui/backend/`.
Use `open-webui/backend-files.txt` for the exact source → target paths.

- [ ] Copy `backend/utils/rocketchat.py`            → `backend/open_webui/utils/`
- [ ] Copy `backend/utils/rocketchat_sync.py`       → `backend/open_webui/utils/`
- [ ] Copy `backend/utils/rocketchat_bridge.py`     → `backend/open_webui/utils/`
- [ ] Copy `backend/utils/rc_sync_queue.py`         → `backend/open_webui/utils/`
- [ ] Copy `backend/utils/ddp_client.py`            → `backend/open_webui/utils/`
- [ ] Copy `backend/routers/rocketchat_extras.py`         → `backend/open_webui/routers/`
- [ ] Copy `backend/routers/rocketchat_integrations.py`   → `backend/open_webui/routers/`
- [ ] Copy `backend/routers/oauth_server.py`              → `backend/open_webui/routers/`

## Phase 3 — Apply backend merge snippets

Snippet files live in `deploy/rocketchat/open-webui/backend/snippets/`.
Each snippet's header says exactly where to insert it.

- [ ] Apply `backend/snippets/env.py.snippet`      — append at end of `env.py`
- [ ] Apply `backend/snippets/main.py.snippet`     — 6 insertion points in `main.py`
- [ ] Apply `backend/snippets/auths.py.snippet`    — 2 insertions in `routers/auths.py`
- [ ] Apply `backend/snippets/users.py.snippet`    — imports + enqueues + new endpoints in `users.py`
- [ ] Apply `backend/snippets/channels.py.snippet` — imports + enqueues + search + new endpoints in `channels.py`

## Phase 4 — Copy + merge frontend code

Frontend source lives in `deploy/rocketchat/open-webui/frontend/`.
Use `open-webui/frontend-files.txt` for the exact source → target paths.

- [ ] Copy `frontend/lib/apis/rocketchat.ts`           → `src/lib/apis/rocketchat.ts`
- [ ] Copy `frontend/lib/utils/push.ts`                → `src/lib/utils/push.ts`
- [ ] Copy `frontend/routes/admin/rocketchat/+page.svelte` → `src/routes/(app)/admin/rocketchat/+page.svelte`
- [ ] Merge the `{#if $config?.features?.rocketchat_enabled}` blocks (see patch-notes.md items 14–17)
      into: admin layout, root layout, search page, MessageInput, Message, Navbar,
      HighlightsModal, URLPreview, UserList, Settings/General, users API.

## Phase 5 — Configure environment

- [ ] `cp deploy/rocketchat/.env.example .env`
- [ ] Replace every `change-me` secret (generate real random values).
- [ ] Replace every `example.com` domain with the real ones.
- [ ] Pick the right URL topology from `README.md` (local / VPS / external RC).
- [ ] Pin `ROCKETCHAT_TAG` and `MONGO_TAG`.
- [ ] Run `scripts/validate-env.sh` (or `.ps1`) — fix all FAIL items.

## Phase 6 — Wire the compose layering

Decide which case applies:

- [ ] **Bundled RC** → include `compose.yaml` + `compose.open-webui.override.yaml`
      with `--profile rocketchat`.
- [ ] **External RC** → include only `compose.open-webui.override.yaml`; point
      `ROCKETCHAT_*_URL` at the existing server; configure its OAuth provider.
- [ ] Confirm the target's base `docker-compose.yaml` does **not** hard-depend on
      Rocket.Chat (no `depends_on: rocketchat`, no required RC secrets).

## Phase 7 — First run

- [ ] `docker compose ... --profile rocketchat up -d`
- [ ] `docker compose ps` — all services healthy (RC `start_period` is ~90s).
- [ ] Rocket.Chat reached **without** the setup wizard (auto-provisioned admin).

## Phase 8 — Smoke test

- [ ] `scripts/smoke-test.sh` passes all 6 checks.
- [ ] In a browser: open Open WebUI, log in.
- [ ] Open Rocket.Chat, click "Sign in with Open WebUI" → lands logged in.
- [ ] Send a message in an OW channel → it appears in the matching RC room.
- [ ] (No manual editing of Rocket.Chat admin settings was needed.)

## Phase 9 — Acceptance criteria (all must hold)

- [ ] Fresh clone can copy `deploy/rocketchat/` as a unit.
- [ ] User fills one `.env`.
- [ ] User runs one documented compose command.
- [ ] Open WebUI boots without the RC package included.
- [ ] Rocket.Chat boots without the interactive setup wizard.
- [ ] OAuth login works without manually editing RC admin settings.
- [ ] No hardcoded local-only hostnames required for production.
- [ ] No fixed `container_name` values.
- [ ] No `:latest` image tags.
- [ ] No required Rocket.Chat secrets when RC is not enabled.
- [ ] Smoke test passes.
