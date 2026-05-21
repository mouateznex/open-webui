"""
Rocket.Chat REST API client.

Authenticates as a service-account admin and exposes async helpers used
by the integration layer (user sync, channel sync, role propagation, etc.).

The client keeps a single shared httpx.AsyncClient and re-authenticates
automatically when the Rocket.Chat auth token expires (6-hour default TTL).
"""

import logging
import time
from typing import Any, Optional

import httpx

log = logging.getLogger(__name__)

# Rocket.Chat auth tokens are valid for 6 hours by default; we refresh
# proactively 5 minutes before expiry.
_TOKEN_TTL = 6 * 3600
_REFRESH_BUFFER = 300


class RocketChatError(Exception):
    """Raised when the Rocket.Chat API returns a non-success response."""
    def __init__(self, status: int, error: str):
        self.status = status
        self.error = error
        super().__init__(f'Rocket.Chat API error {status}: {error}')


class RocketChatClient:
    def __init__(self, base_url: str, admin_user: str, admin_password: str):
        self._base = base_url.rstrip('/')
        self._admin_user = admin_user
        self._admin_password = admin_password

        self._auth_token: Optional[str] = None
        self._user_id: Optional[str] = None
        self._token_expires_at: float = 0.0

        self._http = httpx.AsyncClient(timeout=15.0)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _ensure_auth(self) -> None:
        if self._auth_token and time.time() < self._token_expires_at - _REFRESH_BUFFER:
            return
        await self._login()

    async def _login(self) -> None:
        resp = await self._http.post(
            f'{self._base}/api/v1/login',
            json={'user': self._admin_user, 'password': self._admin_password},
        )
        body = resp.json()
        if resp.status_code != 200 or body.get('status') != 'success':
            raise RocketChatError(resp.status_code, body.get('message', 'login failed'))
        data = body['data']
        self._auth_token = data['authToken']
        self._user_id = data['userId']
        self._token_expires_at = time.time() + _TOKEN_TTL
        log.info('Rocket.Chat service account authenticated (userId=%s)', self._user_id)

    def _headers(self) -> dict:
        return {
            'X-Auth-Token': self._auth_token,
            'X-User-Id': self._user_id,
            'Content-Type': 'application/json',
        }

    async def _get(self, path: str, params: Optional[dict] = None) -> dict:
        await self._ensure_auth()
        resp = await self._http.get(
            f'{self._base}/api/v1/{path}',
            headers=self._headers(),
            params=params,
        )
        return self._unwrap(resp)

    async def _post(self, path: str, payload: Optional[dict] = None) -> dict:
        await self._ensure_auth()
        resp = await self._http.post(
            f'{self._base}/api/v1/{path}',
            headers=self._headers(),
            json=payload or {},
        )
        return self._unwrap(resp)

    @staticmethod
    def _unwrap(resp: httpx.Response) -> dict:
        try:
            body = resp.json()
        except Exception:
            body = {}
        if resp.status_code >= 400 or body.get('success') is False:
            error = (
                body.get('error')
                or body.get('message')
                or body.get('errorType')
                or resp.text[:200]
            )
            raise RocketChatError(resp.status_code, error)
        return body

    # ------------------------------------------------------------------
    # User management
    # ------------------------------------------------------------------

    async def create_user(
        self,
        email: str,
        name: str,
        username: str,
        password: str,
        roles: Optional[list] = None,
    ) -> dict:
        """Create a Rocket.Chat user. Returns the created user object."""
        payload: dict[str, Any] = {
            'email': email,
            'name': name,
            'username': username,
            'password': password,
            'roles': roles or ['user'],
            'verified': True,
            'joinDefaultChannels': True,
            'requirePasswordChange': False,
            'sendWelcomeEmail': False,
        }
        body = await self._post('users.create', payload)
        return body.get('user', {})

    async def update_user(self, rc_user_id: str, data: dict) -> dict:
        """Update a Rocket.Chat user by their Rocket.Chat user ID."""
        body = await self._post('users.update', {'userId': rc_user_id, 'data': data})
        return body.get('user', {})

    async def delete_user(self, rc_user_id: str) -> bool:
        await self._post('users.delete', {'userId': rc_user_id})
        return True

    async def set_user_roles(self, rc_user_id: str, roles: list) -> dict:
        """Replace the roles on a Rocket.Chat user."""
        return await self.update_user(rc_user_id, {'roles': roles})

    async def get_user_by_email(self, email: str) -> Optional[dict]:
        """Return the Rocket.Chat user record for an email, or None."""
        try:
            body = await self._get('users.info', {'email': email})
            return body.get('user')
        except RocketChatError as e:
            if 'User not found' in e.error or e.status == 404:
                return None
            raise

    async def get_user_by_id(self, rc_user_id: str) -> Optional[dict]:
        try:
            body = await self._get('users.info', {'userId': rc_user_id})
            return body.get('user')
        except RocketChatError as e:
            if 'User not found' in e.error or e.status == 404:
                return None
            raise

    async def set_user_active(self, rc_user_id: str, active: bool) -> dict:
        return await self._post('users.setActiveStatus', {
            'userId': rc_user_id,
            'activeStatus': active,
        })

    # ------------------------------------------------------------------
    # Channel management (used by Phase 3)
    # ------------------------------------------------------------------

    async def create_channel(self, name: str, members: Optional[list] = None, read_only: bool = False) -> dict:
        body = await self._post('channels.create', {
            'name': name,
            'members': members or [],
            'readOnly': read_only,
        })
        return body.get('channel', {})

    async def delete_channel(self, room_id: str) -> bool:
        await self._post('channels.delete', {'roomId': room_id})
        return True

    async def get_channel_info(self, room_id: str) -> Optional[dict]:
        try:
            body = await self._get('channels.info', {'roomId': room_id})
            return body.get('channel')
        except RocketChatError as e:
            if e.status == 400:
                return None
            raise

    # ------------------------------------------------------------------
    # Misc
    # ------------------------------------------------------------------

    async def server_info(self) -> dict:
        """Return Rocket.Chat server info (no auth required)."""
        resp = await self._http.get(f'{self._base}/api/v1/info')
        return resp.json()

    async def close(self) -> None:
        await self._http.aclose()


# ---------------------------------------------------------------------------
# Module-level singleton — initialised in main.py lifespan from env vars.
# Code elsewhere imports `rocketchat_client` and checks `is_configured()`.
# ---------------------------------------------------------------------------

_client: Optional[RocketChatClient] = None


def init(base_url: str, admin_user: str, admin_password: str) -> None:
    global _client
    if base_url and admin_user and admin_password:
        _client = RocketChatClient(base_url, admin_user, admin_password)
        log.info('Rocket.Chat client initialised (url=%s)', base_url)
    else:
        log.info('Rocket.Chat integration disabled (ROCKETCHAT_URL / credentials not set)')


def is_configured() -> bool:
    return _client is not None


def get_client() -> RocketChatClient:
    if _client is None:
        raise RuntimeError('Rocket.Chat client is not initialised')
    return _client
