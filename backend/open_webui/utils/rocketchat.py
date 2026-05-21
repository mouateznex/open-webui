"""
Rocket.Chat REST API client.

Authenticates as a service-account admin and exposes async helpers used
by the integration layer (user sync, channel sync, role propagation, etc.).

The client keeps a single shared httpx.AsyncClient and re-authenticates
automatically when the Rocket.Chat auth token expires (6-hour default TTL).

This file is intentionally a *thin* wrapper — every method maps to a single
Rocket.Chat REST endpoint and returns the parsed JSON. Higher-level orchestration
(sync queues, retries, bridges, OW model translation) lives elsewhere.
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

        self._http = httpx.AsyncClient(timeout=30.0)

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
        try:
            body = resp.json()
        except Exception:
            body = {}
        if resp.status_code != 200 or body.get('status') != 'success':
            raise RocketChatError(resp.status_code, body.get('message', 'login failed'))
        data = body['data']
        self._auth_token = data['authToken']
        self._user_id = data['userId']
        self._token_expires_at = time.time() + _TOKEN_TTL
        log.info('Rocket.Chat service account authenticated (userId=%s)', self._user_id)

    def _headers(self, content_type: Optional[str] = 'application/json') -> dict:
        h = {
            'X-Auth-Token': self._auth_token or '',
            'X-User-Id': self._user_id or '',
        }
        if content_type:
            h['Content-Type'] = content_type
        return h

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

    async def _delete(self, path: str, params: Optional[dict] = None) -> dict:
        await self._ensure_auth()
        resp = await self._http.delete(
            f'{self._base}/api/v1/{path}',
            headers=self._headers(),
            params=params,
        )
        return self._unwrap(resp)

    async def _upload(self, path: str, *, files: dict, data: Optional[dict] = None) -> dict:
        await self._ensure_auth()
        # Don't set Content-Type — httpx will compute the multipart boundary.
        resp = await self._http.post(
            f'{self._base}/api/v1/{path}',
            headers=self._headers(content_type=None),
            files=files,
            data=data or {},
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

    # ==================================================================
    # User management
    # ==================================================================

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

    async def deactivate_user(self, rc_user_id: str) -> bool:
        """
        Permanently deactivate a user (cannot log in, cannot post, kicked from
        rooms). Equivalent to admin → users → "Deactivate".
        """
        await self._post('users.deactivateIdle', {'userId': rc_user_id})
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

    async def set_user_avatar(self, rc_user_id: str, avatar_url: str) -> bool:
        """Update a user's avatar from a URL."""
        await self._post('users.setAvatar', {'userId': rc_user_id, 'avatarUrl': avatar_url})
        return True

    async def reset_user_avatar(self, rc_user_id: str) -> bool:
        await self._post('users.resetAvatar', {'userId': rc_user_id})
        return True

    async def set_user_preferences(self, rc_user_id: str, prefs: dict) -> dict:
        """Push notification preferences. Mirrors users.setPreferences."""
        body = await self._post('users.setPreferences', {'userId': rc_user_id, 'data': prefs})
        return body.get('user', {})

    async def get_user_preferences(self, rc_user_id: str) -> dict:
        body = await self._get('users.getPreferences', {'userId': rc_user_id})
        return body.get('preferences', {})

    # ------------------------------------------------------------------
    # Push notifications
    # ------------------------------------------------------------------

    async def push_register_token(self, rc_user_id: str, app_name: str, token: str, platform: str) -> bool:
        """Register a mobile push token for a user (RC's gateway integration)."""
        await self._post('push.token', {
            'userId': rc_user_id,
            'type': platform,           # 'gcm' or 'apn'
            'value': token,
            'appName': app_name,
        })
        return True

    async def push_unregister_token(self, token: str) -> bool:
        await self._delete('push.token', {'token': token})
        return True

    # ==================================================================
    # Channel management — public channels
    # ==================================================================

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

    async def rename_channel(self, room_id: str, name: str) -> dict:
        body = await self._post('channels.rename', {'roomId': room_id, 'name': name})
        return body.get('channel', {})

    async def set_channel_description(self, room_id: str, description: str) -> dict:
        body = await self._post('channels.setDescription', {'roomId': room_id, 'description': description})
        return body.get('channel', {})

    async def set_channel_topic(self, room_id: str, topic: str) -> dict:
        body = await self._post('channels.setTopic', {'roomId': room_id, 'topic': topic})
        return body.get('channel', {})

    async def set_channel_announcement(self, room_id: str, announcement: str) -> dict:
        body = await self._post('channels.setAnnouncement', {'roomId': room_id, 'announcement': announcement})
        return body.get('channel', {})

    async def set_channel_read_only(self, room_id: str, read_only: bool) -> dict:
        body = await self._post('channels.setReadOnly', {'roomId': room_id, 'readOnly': read_only})
        return body.get('channel', {})

    async def set_channel_join_code(self, room_id: str, join_code: str) -> dict:
        body = await self._post('channels.setJoinCode', {'roomId': room_id, 'joinCode': join_code})
        return body.get('channel', {})

    async def set_channel_default(self, room_id: str, default: bool) -> dict:
        body = await self._post('channels.setDefault', {'roomId': room_id, 'default': default})
        return body.get('channel', {})

    async def archive_channel(self, room_id: str) -> bool:
        await self._post('channels.archive', {'roomId': room_id})
        return True

    async def unarchive_channel(self, room_id: str) -> bool:
        await self._post('channels.unarchive', {'roomId': room_id})
        return True

    async def add_channel_owner(self, room_id: str, user_id: str) -> bool:
        await self._post('channels.addOwner', {'roomId': room_id, 'userId': user_id})
        return True

    async def remove_channel_owner(self, room_id: str, user_id: str) -> bool:
        await self._post('channels.removeOwner', {'roomId': room_id, 'userId': user_id})
        return True

    async def add_channel_moderator(self, room_id: str, user_id: str) -> bool:
        await self._post('channels.addModerator', {'roomId': room_id, 'userId': user_id})
        return True

    async def remove_channel_moderator(self, room_id: str, user_id: str) -> bool:
        await self._post('channels.removeModerator', {'roomId': room_id, 'userId': user_id})
        return True

    async def add_channel_leader(self, room_id: str, user_id: str) -> bool:
        await self._post('channels.addLeader', {'roomId': room_id, 'userId': user_id})
        return True

    async def remove_channel_leader(self, room_id: str, user_id: str) -> bool:
        await self._post('channels.removeLeader', {'roomId': room_id, 'userId': user_id})
        return True

    async def invite_to_channel(self, room_id: str, user_id: str) -> dict:
        body = await self._post('channels.invite', {'roomId': room_id, 'userId': user_id})
        return body.get('channel', {})

    async def kick_from_channel(self, room_id: str, user_id: str) -> dict:
        body = await self._post('channels.kick', {'roomId': room_id, 'userId': user_id})
        return body.get('channel', {})

    async def get_channel_info(self, room_id: str) -> Optional[dict]:
        try:
            body = await self._get('channels.info', {'roomId': room_id})
            return body.get('channel')
        except RocketChatError as e:
            if e.status == 400 or e.status == 404:
                return None
            raise

    async def list_channels(self, count: int = 100, offset: int = 0) -> list:
        body = await self._get('channels.list', {'count': count, 'offset': offset})
        return body.get('channels', [])

    # ==================================================================
    # Channel management — private groups
    # ==================================================================

    async def create_group(self, name: str, members: Optional[list] = None, read_only: bool = False) -> dict:
        body = await self._post('groups.create', {
            'name': name,
            'members': members or [],
            'readOnly': read_only,
        })
        return body.get('group', {})

    async def delete_group(self, room_id: str) -> bool:
        await self._post('groups.delete', {'roomId': room_id})
        return True

    async def rename_group(self, room_id: str, name: str) -> dict:
        body = await self._post('groups.rename', {'roomId': room_id, 'name': name})
        return body.get('group', {})

    async def set_group_description(self, room_id: str, description: str) -> dict:
        body = await self._post('groups.setDescription', {'roomId': room_id, 'description': description})
        return body.get('group', {})

    async def set_group_topic(self, room_id: str, topic: str) -> dict:
        body = await self._post('groups.setTopic', {'roomId': room_id, 'topic': topic})
        return body.get('group', {})

    async def set_group_announcement(self, room_id: str, announcement: str) -> dict:
        body = await self._post('groups.setAnnouncement', {'roomId': room_id, 'announcement': announcement})
        return body.get('group', {})

    async def set_group_read_only(self, room_id: str, read_only: bool) -> dict:
        body = await self._post('groups.setReadOnly', {'roomId': room_id, 'readOnly': read_only})
        return body.get('group', {})

    async def archive_group(self, room_id: str) -> bool:
        await self._post('groups.archive', {'roomId': room_id})
        return True

    async def unarchive_group(self, room_id: str) -> bool:
        await self._post('groups.unarchive', {'roomId': room_id})
        return True

    async def add_group_owner(self, room_id: str, user_id: str) -> bool:
        await self._post('groups.addOwner', {'roomId': room_id, 'userId': user_id})
        return True

    async def add_group_moderator(self, room_id: str, user_id: str) -> bool:
        await self._post('groups.addModerator', {'roomId': room_id, 'userId': user_id})
        return True

    async def add_group_leader(self, room_id: str, user_id: str) -> bool:
        await self._post('groups.addLeader', {'roomId': room_id, 'userId': user_id})
        return True

    async def invite_to_group(self, room_id: str, user_id: str) -> dict:
        body = await self._post('groups.invite', {'roomId': room_id, 'userId': user_id})
        return body.get('group', {})

    async def kick_from_group(self, room_id: str, user_id: str) -> dict:
        body = await self._post('groups.kick', {'roomId': room_id, 'userId': user_id})
        return body.get('group', {})

    async def get_group_info(self, room_id: str) -> Optional[dict]:
        try:
            body = await self._get('groups.info', {'roomId': room_id})
            return body.get('group')
        except RocketChatError as e:
            if e.status in (400, 404):
                return None
            raise

    # ==================================================================
    # Direct messages
    # ==================================================================

    async def create_dm(self, usernames: list) -> dict:
        """
        Create or open a DM between the authenticated admin user and the
        given usernames (or between two users if both supplied via the
        admin-only ``users`` form).
        """
        body = await self._post('im.create', {'usernames': ','.join(usernames)})
        return body.get('room', {})

    async def get_dm_info(self, room_id: str) -> Optional[dict]:
        try:
            body = await self._get('im.info', {'roomId': room_id})
            return body.get('room')
        except RocketChatError as e:
            if e.status in (400, 404):
                return None
            raise

    async def close_dm(self, room_id: str) -> bool:
        await self._post('im.close', {'roomId': room_id})
        return True

    # ==================================================================
    # Generic rooms
    # ==================================================================

    async def get_room_info(self, room_id: str) -> Optional[dict]:
        try:
            body = await self._get('rooms.info', {'roomId': room_id})
            return body.get('room')
        except RocketChatError as e:
            if e.status in (400, 404):
                return None
            raise

    async def list_rooms_admin(self, types: Optional[list] = None, count: int = 100, offset: int = 0) -> list:
        params: dict = {'count': count, 'offset': offset}
        if types:
            params['types[]'] = types
        body = await self._get('rooms.adminRooms', params)
        return body.get('rooms', [])

    async def upload_to_room(
        self,
        room_id: str,
        filename: str,
        content: bytes,
        content_type: str = 'application/octet-stream',
        description: Optional[str] = None,
        msg: Optional[str] = None,
        thread_id: Optional[str] = None,
    ) -> dict:
        """
        POST /api/v1/rooms.upload/:roomId — upload a file (image, audio, video,
        any binary) into a Rocket.Chat room. Returns the resulting message.
        """
        files = {'file': (filename, content, content_type)}
        data: dict = {}
        if description:
            data['description'] = description
        if msg:
            data['msg'] = msg
        if thread_id:
            data['tmid'] = thread_id
        body = await self._upload(f'rooms.upload/{room_id}', files=files, data=data)
        return body.get('message', {})

    async def get_room_url_preview(self, room_id: str, url: str) -> dict:
        body = await self._get('chat.getURLPreview', {'roomId': room_id, 'url': url})
        return body.get('urlPreview', {})

    # ==================================================================
    # Messaging
    # ==================================================================

    async def send_message(
        self,
        room_id: str,
        text: str,
        custom_fields: Optional[dict] = None,
        thread_id: Optional[str] = None,
        alias: Optional[str] = None,
        avatar: Optional[str] = None,
        attachments: Optional[list] = None,
    ) -> dict:
        """Post a message to a Rocket.Chat room via REST."""
        payload: dict = {'rid': room_id, 'msg': text}
        if custom_fields:
            payload['customFields'] = custom_fields
        if thread_id:
            payload['tmid'] = thread_id
        if alias:
            payload['alias'] = alias
        if avatar:
            payload['avatar'] = avatar
        if attachments is not None:
            payload['attachments'] = attachments
        body = await self._post('chat.sendMessage', {'message': payload})
        return body.get('message', {})

    async def post_message_as(
        self,
        room_id: str,
        text: str,
        alias: Optional[str] = None,
        avatar: Optional[str] = None,
        emoji: Optional[str] = None,
    ) -> dict:
        """Bot-style message post (chat.postMessage) — used for AI bot replies."""
        payload: dict = {'roomId': room_id, 'text': text}
        if alias:
            payload['alias'] = alias
        if avatar:
            payload['avatar'] = avatar
        if emoji:
            payload['emoji'] = emoji
        body = await self._post('chat.postMessage', payload)
        return body.get('message', {})

    async def update_message(self, room_id: str, message_id: str, text: str) -> dict:
        body = await self._post('chat.update', {'roomId': room_id, 'msgId': message_id, 'text': text})
        return body.get('message', {})

    async def delete_message(self, room_id: str, message_id: str, as_user: bool = False) -> bool:
        await self._post('chat.delete', {'roomId': room_id, 'msgId': message_id, 'asUser': as_user})
        return True

    async def react_to_message(self, message_id: str, emoji: str, should_react: bool = True) -> bool:
        await self._post('chat.react', {
            'messageId': message_id,
            'emoji': emoji,
            'shouldReact': should_react,
        })
        return True

    async def star_message(self, message_id: str) -> bool:
        await self._post('chat.starMessage', {'messageId': message_id})
        return True

    async def unstar_message(self, message_id: str) -> bool:
        await self._post('chat.unStarMessage', {'messageId': message_id})
        return True

    async def get_starred_messages(self, room_id: str, count: int = 50, offset: int = 0) -> list:
        body = await self._get('chat.getStarredMessages', {'roomId': room_id, 'count': count, 'offset': offset})
        return body.get('messages', [])

    async def pin_message(self, message_id: str) -> bool:
        await self._post('chat.pinMessage', {'messageId': message_id})
        return True

    async def unpin_message(self, message_id: str) -> bool:
        await self._post('chat.unPinMessage', {'messageId': message_id})
        return True

    async def get_pinned_messages(self, room_id: str, count: int = 50, offset: int = 0) -> list:
        body = await self._get('chat.getPinnedMessages', {'roomId': room_id, 'count': count, 'offset': offset})
        return body.get('messages', [])

    async def get_thread_messages(self, thread_id: str, count: int = 50, offset: int = 0) -> list:
        body = await self._get('chat.getThreadMessages', {'tmid': thread_id, 'count': count, 'offset': offset})
        return body.get('messages', [])

    async def get_thread_list(self, room_id: str, count: int = 50, offset: int = 0) -> list:
        body = await self._get('chat.getThreadsList', {'rid': room_id, 'count': count, 'offset': offset})
        return body.get('threads', [])

    async def get_mentions(self, room_id: str, count: int = 50, offset: int = 0) -> list:
        body = await self._get('channels.getAllUserMentionsByChannel', {
            'roomId': room_id, 'count': count, 'offset': offset,
        })
        return body.get('mentions', [])

    async def search_messages(self, room_id: str, query: str, count: int = 50) -> list:
        """Full-text search within a Rocket.Chat room via chat.search."""
        body = await self._get('chat.search', {'roomId': room_id, 'searchText': query, 'count': count})
        return body.get('messages', [])

    async def search_global(self, query: str, count: int = 50, offset: int = 0) -> list:
        """
        Server-wide message search (Spotlight). Returns messages across every
        room the authenticated user can read.
        """
        body = await self._get('spotlight', {'query': query})
        # Spotlight returns rooms + users, but we also fetch matching messages.
        users = body.get('users', [])
        rooms = body.get('rooms', [])
        # Use chat.search across the top spotlight rooms for actual messages.
        messages: list = []
        for r in rooms[:5]:
            try:
                hits = await self.search_messages(r['_id'], query, count=count)
                messages.extend(hits)
            except RocketChatError:
                continue
        return [{'users': users, 'rooms': rooms, 'messages': messages[:count + offset]}]

    # ==================================================================
    # Presence / status
    # ==================================================================

    async def set_user_status(
        self,
        rc_user_id: str,
        status: str,
        message: Optional[str] = None,
    ) -> bool:
        payload: dict = {'userId': rc_user_id, 'status': status}
        if message is not None:
            payload['message'] = message
        await self._post('users.setStatus', payload)
        return True

    async def get_user_presence(self, rc_user_id: str) -> dict:
        body = await self._get('users.getStatus', {'userId': rc_user_id})
        return {'status': body.get('status'), 'message': body.get('message'), 'connectionStatus': body.get('connectionStatus')}

    # ==================================================================
    # Subscriptions / unread counts
    # ==================================================================

    async def get_subscriptions(self, rc_user_id: Optional[str] = None) -> list:
        params = {}
        if rc_user_id:
            # The /api/v1/subscriptions.getAll endpoint uses the auth token's user.
            # For other users, fall back to subscriptions.getOne per room (caller must loop).
            params['userId'] = rc_user_id
        body = await self._get('subscriptions.getAll', params)
        return body.get('update', [])

    async def get_subscription_one(self, room_id: str) -> Optional[dict]:
        try:
            body = await self._get('subscriptions.getOne', {'roomId': room_id})
            return body.get('subscription')
        except RocketChatError:
            return None

    async def mark_room_read(self, room_id: str) -> bool:
        await self._post('subscriptions.read', {'rid': room_id})
        return True

    async def mark_room_unread(self, room_id: str, first_unread_message_id: Optional[str] = None) -> bool:
        payload: dict = {'roomId': room_id}
        if first_unread_message_id:
            payload['firstUnreadMessage'] = {'_id': first_unread_message_id}
        await self._post('subscriptions.unread', payload)
        return True

    # ==================================================================
    # Omnichannel (LiveChat)
    # ==================================================================

    async def list_livechat_departments(self) -> list:
        body = await self._get('livechat/department')
        return body.get('departments', [])

    async def list_livechat_rooms(self, count: int = 50, offset: int = 0) -> list:
        body = await self._get('livechat/rooms', {'count': count, 'offset': offset})
        return body.get('rooms', [])

    async def list_livechat_agents(self) -> list:
        body = await self._get('livechat/users/agent')
        return body.get('users', [])

    async def add_livechat_agent(self, username: str) -> dict:
        body = await self._post('livechat/users/agent', {'username': username})
        return body.get('user', {})

    async def remove_livechat_agent(self, username: str) -> bool:
        await self._delete(f'livechat/users/agent/{username}')
        return True

    async def take_livechat_room(self, room_id: str, agent_username: str) -> bool:
        body = await self._get('livechat/users/agent', {'username': agent_username})
        agent = (body.get('users') or [{}])[0]
        if not agent.get('_id'):
            return False
        await self._post('livechat/room.take', {'roomId': room_id, 'userId': agent['_id']})
        return True

    async def close_livechat_room(self, room_id: str, comment: str = '') -> bool:
        await self._post('livechat/room.close', {'rid': room_id, 'comment': comment})
        return True

    # ==================================================================
    # Webhooks (incoming/outgoing) — managed through RC's integrations API
    # ==================================================================

    async def list_integrations(self, count: int = 100, offset: int = 0) -> list:
        body = await self._get('integrations.list', {'count': count, 'offset': offset})
        return body.get('integrations', [])

    async def create_incoming_webhook(
        self,
        name: str,
        channel: str,
        username: str,
        emoji: Optional[str] = None,
        avatar: Optional[str] = None,
        script: Optional[str] = None,
        enabled: bool = True,
    ) -> dict:
        body = await self._post('integrations.create', {
            'type': 'webhook-incoming',
            'name': name,
            'channel': channel,
            'username': username,
            'emoji': emoji or '',
            'avatar': avatar or '',
            'scriptEnabled': bool(script),
            'script': script or '',
            'enabled': enabled,
            'alias': '',
            'overrideDestinationChannelEnabled': False,
        })
        return body.get('integration', {})

    async def create_outgoing_webhook(
        self,
        name: str,
        urls: list,
        channel: str,
        trigger_words: Optional[list] = None,
        username: Optional[str] = None,
        token: Optional[str] = None,
        enabled: bool = True,
    ) -> dict:
        body = await self._post('integrations.create', {
            'type': 'webhook-outgoing',
            'name': name,
            'event': 'sendMessage',
            'channel': channel,
            'username': username or '',
            'urls': urls,
            'triggerWords': trigger_words or [],
            'token': token or '',
            'enabled': enabled,
            'alias': '',
            'avatar': '',
            'emoji': '',
            'scriptEnabled': False,
            'script': '',
            'impersonateUser': False,
            'retryFailedCalls': True,
            'retryCount': 6,
            'retryDelay': 'powers-of-ten',
        })
        return body.get('integration', {})

    async def delete_integration(self, integration_id: str, integration_type: str = 'webhook-incoming') -> bool:
        await self._post('integrations.remove', {
            'type': integration_type,
            'integrationId': integration_id,
        })
        return True

    async def update_integration(self, integration_id: str, integration_type: str, data: dict) -> dict:
        payload = {'type': integration_type, 'integrationId': integration_id, **data}
        body = await self._post('integrations.update', payload)
        return body.get('integration', {})

    async def post_to_incoming_webhook(self, webhook_url: str, payload: dict) -> dict:
        """
        Forward a payload to one of RC's *incoming* webhook URLs.
        ``webhook_url`` is the absolute URL RC generated for the integration.
        """
        resp = await self._http.post(webhook_url, json=payload)
        try:
            return resp.json() if resp.content else {}
        except Exception:
            return {'status_code': resp.status_code}

    # ==================================================================
    # Marketplace / Apps Engine
    # ==================================================================

    async def list_apps(self) -> list:
        try:
            body = await self._get('apps')
            return body.get('apps', [])
        except RocketChatError:
            return []

    async def get_app(self, app_id: str) -> Optional[dict]:
        try:
            body = await self._get(f'apps/{app_id}')
            return body.get('app')
        except RocketChatError:
            return None

    async def list_marketplace_apps(self) -> list:
        try:
            body = await self._get('apps/marketplace')
            return body if isinstance(body, list) else body.get('apps', [])
        except RocketChatError:
            return []

    async def list_slash_commands(self) -> list:
        body = await self._get('commands.list')
        return body.get('commands', [])

    async def run_slash_command(self, room_id: str, command: str, params: str = '') -> dict:
        body = await self._post('commands.run', {
            'command': command,
            'roomId': room_id,
            'params': params,
        })
        return body.get('result', {})

    # ==================================================================
    # Audit logs
    # ==================================================================

    async def get_audit_logs(self, start: Optional[int] = None, end: Optional[int] = None,
                             user: Optional[str] = None, count: int = 100) -> list:
        params: dict = {'count': count}
        if start is not None:
            params['startDate'] = start
        if end is not None:
            params['endDate'] = end
        if user:
            params['users[]'] = user
        try:
            body = await self._get('audit', params)
            return body.get('audits', body.get('logs', []))
        except RocketChatError:
            return []

    async def get_message_audit_logs(self, room_id: Optional[str] = None, count: int = 100) -> list:
        params: dict = {'count': count}
        if room_id:
            params['rids'] = room_id
        try:
            body = await self._get('audit.messages', params)
            return body.get('messages', [])
        except RocketChatError:
            return []

    # ==================================================================
    # Video conferencing (Jitsi / BBB)
    # ==================================================================

    async def start_video_conference(self, room_id: str, allow_ringing: bool = True) -> dict:
        """
        POST /api/v1/video-conference.jitsi.update — generate a new Jitsi URL
        and open the call indicator in RC for everyone in the room.
        Falls back to the Jitsi room URL if RC's video plugin isn't configured.
        """
        try:
            body = await self._post('video-conference.start', {
                'roomId': room_id,
                'allowRinging': allow_ringing,
            })
            return body.get('data', body)
        except RocketChatError as e:
            log.debug('video-conference.start unavailable (%s); caller may construct URL manually', e)
            return {}

    async def join_video_conference(self, call_id: str) -> dict:
        try:
            body = await self._post('video-conference.join', {'callId': call_id})
            return body.get('data', body)
        except RocketChatError:
            return {}

    # ==================================================================
    # Matrix Federation
    # ==================================================================

    async def is_matrix_federation_enabled(self) -> bool:
        try:
            body = await self._get('settings/Feature_Federation_Matrix_Enabled')
            return bool(body.get('value'))
        except RocketChatError:
            return False

    async def get_federation_room_peers(self, room_id: str) -> list:
        try:
            body = await self._get('rooms.info', {'roomId': room_id})
            room = body.get('room', {})
            # Both naming conventions appear in RC versions
            return room.get('federatedPeers') or room.get('federation', {}).get('peers') or []
        except RocketChatError:
            return []

    async def list_federation_servers(self) -> list:
        """Return the list of federated peer servers known to RC."""
        try:
            body = await self._get('federation/listServersByUser')
            return body.get('servers', [])
        except RocketChatError:
            return []

    async def list_federation_rooms(self) -> list:
        """
        Return the list of all federated rooms (both RC-native and remote).
        Falls back to filtering rooms.adminRooms by federated flag if RC has
        no specific endpoint for this.
        """
        try:
            body = await self._get('federation/rooms')
            rooms = body.get('rooms', [])
            if rooms:
                return rooms
        except RocketChatError:
            pass
        # Fallback: scan admin rooms and pick out federated ones.
        try:
            rooms = await self.list_rooms_admin(count=200)
            return [r for r in rooms if r.get('federated') or (r.get('name', '') or '').startswith('!')]
        except RocketChatError:
            return []

    # ==================================================================
    # Teams (Phase 3.4)
    # ==================================================================

    async def list_teams(self, count: int = 100, offset: int = 0) -> list:
        body = await self._get('teams.listAll', {'count': count, 'offset': offset})
        return body.get('teams', [])

    async def create_team(
        self,
        name: str,
        members: Optional[list] = None,
        team_type: int = 0,         # 0 = public, 1 = private
        owner: Optional[str] = None,
    ) -> dict:
        payload: dict = {'name': name, 'type': team_type, 'members': members or []}
        if owner:
            payload['owner'] = owner
        body = await self._post('teams.create', payload)
        return body.get('team', {})

    async def delete_team(self, team_id: str, rooms_to_remove: Optional[list] = None) -> bool:
        payload: dict = {'teamId': team_id}
        if rooms_to_remove:
            payload['roomsToRemove'] = rooms_to_remove
        await self._post('teams.delete', payload)
        return True

    async def add_team_members(self, team_id: str, members: list) -> dict:
        body = await self._post('teams.addMembers', {'teamId': team_id, 'members': members})
        return body

    async def remove_team_member(self, team_id: str, user_id: str) -> bool:
        await self._post('teams.removeMember', {'teamId': team_id, 'userId': user_id})
        return True

    async def list_team_rooms(self, team_id: str) -> list:
        body = await self._get('teams.listRooms', {'teamId': team_id})
        return body.get('rooms', [])

    # ==================================================================
    # Misc
    # ==================================================================

    async def server_info(self) -> dict:
        """Return Rocket.Chat server info (no auth required)."""
        resp = await self._http.get(f'{self._base}/api/v1/info')
        try:
            return resp.json()
        except Exception:
            return {}

    async def admin_settings(self, ids: Optional[list] = None) -> dict:
        params: dict = {}
        if ids:
            params['_id'] = {'$in': ids}
        body = await self._get('settings', params)
        return body

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
