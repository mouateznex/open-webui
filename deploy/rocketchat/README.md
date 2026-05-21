# Rocket.Chat Integration — Portable Deployment Package

This directory is a **self-contained, copy/paste unit** for adding Rocket.Chat to
an Open WebUI deployment. Copy `deploy/rocketchat/` into the target project, fill
in one `.env`, run one documented command.

The base Open WebUI stack runs fine **without** this package — Rocket.Chat is
strictly opt-in.

```
deploy/rocketchat/
├── compose.yaml                       # MongoDB + Rocket.Chat services (profile: rocketchat)
├── compose.open-webui.override.yaml   # adds RC env vars + dependency to open-webui
├── compose.override.example.yaml      # optional local customizations (reverse proxy, limits)
├── .env.example                       # complete, runnable env template
├── package-manifest.json              # auditable inventory of everything in this package
├── README.md                          # this file
├── MIGRATION_CHECKLIST.md             # step-by-step transplant guide
├── scripts/
│   ├── validate-env.sh / .ps1         # pre-deploy config validation
│   └── smoke-test.sh                  # post-deploy end-to-end verification
└── open-webui/
    ├── backend-files.txt              # which backend files to copy vs merge
    ├── frontend-files.txt             # which frontend files to copy vs merge
    └── patch-notes.md                 # exact app-code patches (no vague "merge hooks")
```

---

## Quick start

```bash
# 1. From the project root, create your env file
cp deploy/rocketchat/.env.example .env
#    Edit .env: replace every change-me secret and example.com domain.

# 2. Validate before you deploy
./deploy/rocketchat/scripts/validate-env.sh        # Linux/macOS
#  .\deploy\rocketchat\scripts\validate-env.ps1     # Windows PowerShell

# 3. Bring up the full stack WITH Rocket.Chat
docker compose \
  -f docker-compose.yaml \
  -f deploy/rocketchat/compose.yaml \
  -f deploy/rocketchat/compose.open-webui.override.yaml \
  --profile rocketchat up -d

# 4. Verify it actually works end to end
./deploy/rocketchat/scripts/smoke-test.sh
```

To run Open WebUI **without** Rocket.Chat, just use the base file — no flags, no
`ROCKETCHAT_ENABLED=false` gymnastics, no required RC secrets:

```bash
docker compose -f docker-compose.yaml up -d
```

> Tip: instead of repeating the `-f`/`--profile` flags, set them once:
> ```bash
> export COMPOSE_FILE=docker-compose.yaml:deploy/rocketchat/compose.yaml:deploy/rocketchat/compose.open-webui.override.yaml
> export COMPOSE_PROFILES=rocketchat
> docker compose up -d
> ```

---

## URL topology — the five URLs, kept distinct

Most integration failures come from conflating these. Keep them separate:

| Variable | Audience | Example | Notes |
|---|---|---|---|
| `WEBUI_URL` | Browser | `https://app.example.com` | Public Open WebUI URL; OIDC issuer. |
| `WEBUI_OAUTH_URL` | RC server **and** browser | `https://app.example.com` | RC uses this for authorize (browser) **and** token/userinfo (server). Must resolve identically from both. |
| `ROCKETCHAT_PUBLIC_URL` | Browser | `https://chat.example.com` | RC's `ROOT_URL`. |
| `ROCKETCHAT_INTERNAL_URL` | OW backend | `http://rocketchat:3000` | Container-to-container API calls. Never localhost inside Docker. |
| `ROCKETCHAT_BASE_URL` | Browser | `https://chat.example.com` | OW uses this to build RC deep-links. Usually == `ROCKETCHAT_PUBLIC_URL`. |

### Example A — Local Docker Desktop

The tricky part locally: `WEBUI_OAUTH_URL` must resolve the same from the browser
and from inside the `rocketchat` container. Use `host.docker.internal` and add a
hosts entry so the browser resolves it too.

```ini
WEBUI_URL=http://host.docker.internal:3000
WEBUI_OAUTH_URL=http://host.docker.internal:3000
ROCKETCHAT_PUBLIC_URL=http://localhost:3100
ROCKETCHAT_INTERNAL_URL=http://rocketchat:3000
ROCKETCHAT_BASE_URL=http://localhost:3100
OAUTH_SERVER_REDIRECT_URIS=http://localhost:3100/_oauth/openwebui
```

Add to your host's `/etc/hosts` (or `C:\Windows\System32\drivers\etc\hosts`):

```
127.0.0.1 host.docker.internal
```

### Example B — Single VPS behind a reverse proxy

Public TLS domains for everything; the internal URL stays on the Docker network.

```ini
WEBUI_URL=https://app.example.com
WEBUI_OAUTH_URL=https://app.example.com
ROCKETCHAT_PUBLIC_URL=https://chat.example.com
ROCKETCHAT_INTERNAL_URL=http://rocketchat:3000
ROCKETCHAT_BASE_URL=https://chat.example.com
OAUTH_SERVER_REDIRECT_URIS=https://chat.example.com/_oauth/openwebui
```

See `compose.override.example.yaml` for Traefik labels.

### Example C — Existing external Rocket.Chat

You already run Rocket.Chat elsewhere; only Open WebUI is in this stack. Do **not**
include `compose.yaml` (it would start a second RC). Include only the override and
point the URLs at your existing server.

```ini
WEBUI_URL=https://app.example.com
WEBUI_OAUTH_URL=https://app.example.com
ROCKETCHAT_PUBLIC_URL=https://chat.corp.example.com
ROCKETCHAT_INTERNAL_URL=https://chat.corp.example.com   # reachable from the OW backend
ROCKETCHAT_BASE_URL=https://chat.corp.example.com
OAUTH_SERVER_REDIRECT_URIS=https://chat.corp.example.com/_oauth/openwebui
```

```bash
docker compose -f docker-compose.yaml -f deploy/rocketchat/compose.open-webui.override.yaml up -d
```

You must configure the Custom OAuth ("OpenWebUI") provider on the external RC
manually — see the `OVERWRITE_SETTING_Accounts_OAuth_Custom-OpenWebUI-*` keys in
`compose.yaml` for the exact values.

---

## Environment variables

The complete list, with defaults and which are required, is in `.env.example` and
`package-manifest.json`. Required: the five URLs above plus `WEBUI_SECRET_KEY`,
the three `OAUTH_SERVER_*`, the three `ROCKETCHAT_ADMIN_*`, and `ROCKETCHAT_TAG`.

---

## Moving this into another project

See `MIGRATION_CHECKLIST.md` for the full transplant procedure and
`open-webui/patch-notes.md` for the exact app-code edits.
