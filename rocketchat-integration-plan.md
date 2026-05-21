# Rocket.Chat + Open WebUI Integration Plan

## Core Auth Principle

Open WebUI is the **single source of truth** for identity. Rocket.Chat never has its own login — it defers entirely to Open WebUI via OAuth 2.0. A user's role (`admin` or `user`/employee) in Open WebUI automatically becomes their role in Rocket.Chat on every login.

---

## Phase 0 — Authentication Foundation (Do This First)

This phase must be completed before anything else. Everything else depends on it.

### Step 0.1 — Make Open WebUI an OAuth 2.0 / OIDC Provider

Create a new router `backend/open_webui/routers/oauth_server.py` that adds these endpoints to Open WebUI's FastAPI backend:

| Endpoint | Purpose |
|---|---|
| `GET /oauth/authorize` | Rocket.Chat redirects users here to log in |
| `POST /oauth/token` | Exchanges auth code for access token |
| `GET /oauth/userinfo` | Returns the logged-in user's profile + role |
| `GET /.well-known/openid-configuration` | OIDC discovery document (auto-config for Rocket.Chat) |

**How the login flow works:**

```
User clicks "Login" in Rocket.Chat
  → Rocket.Chat redirects to Open WebUI /oauth/authorize
  → If user is not logged in: Open WebUI shows its own login page
  → User enters email/password (Open WebUI's normal auth)
  → Open WebUI issues a short-lived auth code
  → Redirects back to Rocket.Chat with the code
  → Rocket.Chat calls /oauth/token to exchange for access token
  → Rocket.Chat calls /oauth/userinfo to get name, email, role
  → Rocket.Chat creates or updates the user account automatically
  → User is now logged in to both systems with one set of credentials
```

### Step 0.2 — Role Mapping

The `/oauth/userinfo` endpoint returns a `roles` claim that Rocket.Chat reads:

| Open WebUI Role | Rocket.Chat Role |
|---|---|
| `admin` | `admin` |
| `user` | `user` (employee) |
| `pending` | Login blocked (Rocket.Chat rejects unverified users) |

### Step 0.3 — Register Rocket.Chat as an OAuth Client

Add a small `oauth_clients` table (or use a config env var) to store:

- `client_id` — generated secret string
- `client_secret` — generated secret string
- `redirect_uri` — Rocket.Chat's OAuth callback URL

These values get pasted into Rocket.Chat's admin panel once.

### Step 0.4 — Store Auth Codes Temporarily

Short-lived authorization codes (10-minute TTL) need temporary storage. Use Redis if available (already optional in Open WebUI), or an in-memory store with a background cleanup task.

### Step 0.5 — Configure Rocket.Chat's Custom OAuth

In Rocket.Chat Admin → OAuth → Add Custom OAuth, enter:

| Field | Value |
|---|---|
| Name | `OpenWebUI` |
| Enable | Yes |
| Authorization URL | `http://open-webui:8080/oauth/authorize` |
| Access Token URL | `http://open-webui:8080/oauth/token` |
| User Info URL | `http://open-webui:8080/oauth/userinfo` |
| Client ID | *(from Step 0.3)* |
| Client Secret | *(from Step 0.3)* |
| Roles/Claim | `roles` |
| Admin Role | `admin` |
| Merge users by email | Yes |
| Show login button | No *(Rocket.Chat login page replaced by Open WebUI's)* |

### Step 0.6 — Disable Rocket.Chat's Native Login

In Rocket.Chat Admin → General → Accounts:

- Disable password login
- Disable user self-registration
- This forces 100% of auth through Open WebUI

---

## Phase 1 — Infrastructure Setup

### Step 1.1 — Docker Compose

Extend `docker-compose.yaml` to add:

- `mongodb` service (Rocket.Chat's required database)
- `rocketchat` service pointing `ROOT_URL` at its public address and `MONGO_URL` at MongoDB
- Internal Docker network so Open WebUI and Rocket.Chat can reach each other by container name

### Step 1.2 — Rocket.Chat API Client

Create `backend/open_webui/utils/rocketchat.py` — a thin async HTTP client that wraps Rocket.Chat's REST API using an **admin service account** (not user credentials). This is used by later phases for channel sync, user provisioning, etc.

### Step 1.3 — Environment Variables

Add to Open WebUI's env:

```env
ROCKETCHAT_URL=http://rocketchat:3000
ROCKETCHAT_ADMIN_USER=rc_service_bot
ROCKETCHAT_ADMIN_PASSWORD=...
OAUTH_RC_CLIENT_ID=...
OAUTH_RC_CLIENT_SECRET=...
```

---

## Phase 2 — User Sync

### Step 2.1 — Auto-Provision on First Login

When a user logs into Open WebUI for the first time and Rocket.Chat calls `/oauth/userinfo`, Open WebUI's backend simultaneously calls Rocket.Chat's `users.create` admin API to pre-provision the account with the correct role. This avoids any delay on the Rocket.Chat side.

### Step 2.2 — Role Change Propagation

When an admin changes a user's role in Open WebUI → a background task calls Rocket.Chat `users.setRoles` to sync the change immediately (no waiting for next login).

### Step 2.3 — Account Deletion Sync

When a user is deleted in Open WebUI → also call Rocket.Chat `users.delete` to remove the account there.

### Step 2.4 — Profile Sync

Name, avatar, and email changes in Open WebUI propagate to Rocket.Chat via `users.update`.

---

## Phase 3 — Channels & Rooms

### Step 3.1 — Sync Open WebUI Channels to Rocket.Chat

- On channel create in Open WebUI → call `channels.create` or `groups.create` in Rocket.Chat
- Store Rocket.Chat `roomId` in Open WebUI's `channel.data` JSON field (no schema change needed)

### Step 3.2 — Add Missing Channel Features

Implement the following (currently absent in Open WebUI) by proxying Rocket.Chat API:

| Feature | Rocket.Chat Endpoint |
|---|---|
| Channel topics, announcements, descriptions | `channels.setTopic`, `channels.setAnnouncement` |
| Read-only channels | `channels.setReadOnly` |
| Archive/unarchive | `channels.archive` / `channels.unarchive` |
| Join codes | `channels.setJoinCode` |
| Default channels | `channels.setDefault` |
| Channel roles (owner/moderator/leader) | `channels.addOwner`, `channels.addModerator` |
| Omnichannel (LiveChat) | `/api/v1/livechat/*` |

### Step 3.3 — Direct Messages

- Create DM rooms via Rocket.Chat `dm.create`, store `roomId` in Open WebUI
- Render DM conversations in the existing Open WebUI channel UI

### Step 3.4 — Teams

- Add a "Teams" concept (Rocket.Chat has team rooms) — map to Open WebUI groups
- New route: `/teams` in SvelteKit frontend

---

## Phase 4 — Real-Time Messaging Bridge

### Step 4.1 — Bridge Open WebUI Sockets ↔ Rocket.Chat Realtime API

Rocket.Chat uses DDP (Distributed Data Protocol) over WebSockets.

- In `backend/open_webui/socket/main.py`, add a DDP client that subscribes to Rocket.Chat's `stream-room-messages` subscription
- When a message arrives from Rocket.Chat → emit it to Open WebUI's Socket.IO clients
- When a user sends a message in Open WebUI → forward it to Rocket.Chat via REST `chat.sendMessage`

### Step 4.2 — Message Feature Parity

Implement in Open WebUI's message model and UI:

| Feature | Implementation |
|---|---|
| Message threads | Add `thread_id` to messages model, `chat.getThreadMessages` |
| Message reactions (emoji) | Add reactions to `MessageModel`, `chat.react` |
| Starred messages | `chat.starMessage` endpoint |
| Pinned messages | `chat.pinMessage` endpoint |
| Message editing | Already exists; sync to `chat.update` |
| Message deletion | Sync to `chat.delete` |
| URL previews | `chat.getURLPreview` |
| @mentions | Already partially exists; add proper notification routing |

### Step 4.3 — File & Media Sharing

- Forward file uploads through Open WebUI to Rocket.Chat's `/api/v1/rooms.upload/:roomId`
- Store Rocket.Chat file URLs alongside Open WebUI file references

---

## Phase 5 — Notifications & Presence

### Step 5.1 — User Presence

- Subscribe to Rocket.Chat's `stream-notify-logged` for presence events
- Expose `/api/v1/rocketchat/presence` in Open WebUI backend
- Show online/away/offline indicators in the channel member list UI

### Step 5.2 — Push Notifications

- Implement Rocket.Chat's push gateway integration for mobile push
- Add notification preferences sync: `users.setPreferences` ↔ Open WebUI notification settings

### Step 5.3 — Unread Counts & Badges

- Subscribe to `stream-notify-user` for unread count updates
- Update Open WebUI's channel sidebar badges in real time

---

## Phase 6 — Admin & Moderation

### Step 6.1 — Admin Panel Additions

Since Open WebUI admins are automatically Rocket.Chat admins (via role mapping from Phase 0), they get full Rocket.Chat admin access automatically. No extra work needed for role elevation.

### Step 6.2 — User Management Surface

Add Rocket.Chat moderation actions (ban, deactivate) to Open WebUI's existing `/admin/users` page. Actions apply to both systems simultaneously.

### Step 6.3 — Audit Logs

Pull Rocket.Chat's audit log data and display in Open WebUI admin panel.

---

## Phase 7 — Search

### Step 7.1 — Full-Text Message Search

- Add a search bar to channel views using `chat.search` endpoint
- Support searching by date range, sender, file type

### Step 7.2 — Global Search

- New `/search` route in SvelteKit
- Federate search across Open WebUI chats, channels, and Rocket.Chat rooms simultaneously

---

## Phase 8 — Integrations & Extensibility

### Step 8.1 — Webhooks

- Expose incoming webhook endpoints in Open WebUI that forward to Rocket.Chat's webhook infrastructure
- Extend existing `utils/webhook.py` for the bridge
- UI for managing webhooks in the admin panel

### Step 8.2 — Marketplace Apps (Rocket.Chat Apps Engine)

- Surface a marketplace panel in the Open WebUI admin UI pointing to the Rocket.Chat apps admin page
- Proxy slash commands from Open WebUI's message input to Rocket.Chat's command handler

### Step 8.3 — Bots & AI Integration

- Create a Rocket.Chat bot user that Open WebUI's AI uses to post AI responses into Rocket.Chat rooms (so they appear in both interfaces)

---

## Phase 9 — Voice & Video

### Step 9.1 — Jitsi / BigBlueButton

Rocket.Chat supports Jitsi and BigBlueButton for video calls:

- Add a "Start Video Call" button in channel/DM views
- Call Rocket.Chat's Jitsi URL generation, open in iframe or new tab

### Step 9.2 — Audio Messages

- Forward audio message recordings to Rocket.Chat's room upload API

---

## Phase 10 — Federation

### Step 10.1 — Matrix Federation

Rocket.Chat supports Matrix protocol federation:

- Configure the Rocket.Chat instance's Matrix bridge
- Expose federated room list in Open WebUI

---

## Phase 11 — Mobile & Desktop Apps

### Step 11.1 — Rocket.Chat Mobile Apps

The official Rocket.Chat iOS/Android apps connect directly to your Rocket.Chat server. They already work once the server is deployed — no extra code needed. Users authenticate via the Open WebUI OAuth flow automatically.

### Step 11.2 — Open WebUI PWA

Open WebUI is already a PWA. Ensure the service worker caches the new channel/chat routes.

---

## Implementation Order

```
Week 1:    Phase 0 — OAuth server in Open WebUI (auth foundation)
Week 2:    Phase 1 — docker-compose + Rocket.Chat API client
Week 3:    Phase 2 — user sync + role propagation
Week 4-5:  Phase 3 — channels
Week 6-7:  Phase 4 — real-time messaging bridge
Week 8:    Phase 5 — notifications + presence
Week 9:    Phase 6 — admin & moderation
Week 10:   Phase 7 — search
Week 11-12: Phase 8 — integrations & webhooks
Week 13:   Phase 9 — voice & video
Week 14:   Phases 10-11 — federation & mobile
```

---

## Key Technical Decisions

| Decision | Recommendation | Reason |
|---|---|---|
| Auth protocol | OAuth 2.0 / OIDC (Open WebUI as provider) | Native Rocket.Chat support, standard protocol |
| DB per service | Keep MongoDB for Rocket.Chat, SQLite/Postgres for Open WebUI | Avoid migration risk; sync via API |
| Real-time | DDP client in Python bridging to Socket.IO | Rocket.Chat uses DDP; Open WebUI uses Socket.IO |
| Message storage | Primary in Rocket.Chat, AI metadata in Open WebUI | Single source of truth for messages |
| File storage | Route all uploads through Rocket.Chat | Single source of truth for attachments |
| Role sync | Push on change (not pull on login) | Immediate effect when admin promotes/demotes a user |

---

## What This Achieves

- **One login page** — Open WebUI's. Rocket.Chat never shows its own.
- **One set of credentials** — email + password stored only in Open WebUI.
- **Automatic role sync** — admins get admin, employees get user, pending users are blocked.
- **No duplicate account management** — create/update/delete in Open WebUI, it propagates automatically.
- **Standard protocol** — uses OAuth 2.0/OIDC, the same mechanism Rocket.Chat already supports natively.
