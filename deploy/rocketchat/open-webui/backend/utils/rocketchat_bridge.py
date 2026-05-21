"""
Rocket.Chat ↔ Open WebUI real-time message bridge.

Direction RC → OW
  The DDP client subscribes to Rocket.Chat's stream-room-messages for every
  channel that has a rocketchat_room_id.  When a message arrives that did NOT
  originate from Open WebUI (identified by the ow_origin custom field), it is
  stored in Open WebUI's messages table and broadcast via Socket.IO so
  connected browsers receive it in real time.

Direction OW → RC
  After post_new_message succeeds in channels.py, forward_to_rc() is called.
  It posts the message to Rocket.Chat via REST (chat.sendMessage) with
  customFields.ow_origin = True so the DDP echo is suppressed.

Presence (Phase 5)
  A single stream-notify-logged subscription receives status changes for all
  users. On each event, the matching Open WebUI user's presence_state is
  updated in the DB and broadcast via Socket.IO to every tab that user has
  open (room user:{id}).

  RC status codes: 0=offline, 1=online, 2=away, 3=busy

Subscription management
  subscribe_channel() is called by the channel router every time a new Open
  WebUI channel gets a Rocket.Chat room ID assigned.
  At bridge start, all existing channels are loaded and subscribed in bulk.
"""

import asyncio
import logging
from typing import Optional

log = logging.getLogger(__name__)

_OW_ORIGIN_FIELD = 'ow_origin'

# RC status code → Open WebUI presence_state string
_RC_STATUS_MAP = {0: 'offline', 1: 'online', 2: 'away', 3: 'busy'}


class RocketChatBridge:
    def __init__(self):
        self._ddp = None
        # Maps RC room ID → Open WebUI channel ID
        self._room_to_channel: dict[str, str] = {}
        # Maps RC room ID → DDP subscription ID
        self._room_to_sub: dict[str, str] = {}
        # Maps RC user ID → Open WebUI user ID (built at start, updated on provision)
        self._rc_to_ow_user: dict[str, str] = {}
        self._running = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self, rc_url: str, admin_user: str, admin_password: str) -> None:
        from open_webui.utils.ddp_client import DDPClient

        ws_url = (
            rc_url.replace('https://', 'wss://')
                  .replace('http://', 'ws://')
                  .rstrip('/')
            + '/websocket'
        )
        self._ddp = DDPClient(ws_url)

        try:
            await self._ddp.connect()
            await self._ddp.login(admin_user, admin_password)
        except Exception as e:
            log.warning('Rocket.Chat bridge failed to start: %s', e)
            return

        await self._build_user_map()
        await self._subscribe_existing_channels()
        await self._subscribe_presence()
        await self._subscribe_unread_counts()
        self._running = True
        log.info('Rocket.Chat real-time bridge running')

    async def stop(self) -> None:
        self._running = False
        if self._ddp:
            await self._ddp.close()

    # ------------------------------------------------------------------
    # User ID reverse map
    # ------------------------------------------------------------------

    async def _build_user_map(self) -> None:
        """Load rc_user_id → ow_user_id from cached info on all OW users."""
        from open_webui.models.users import Users
        try:
            users = await Users.get_users()
            for u in users:
                rc_id = (u.info or {}).get('rocketchat_user_id')
                if rc_id:
                    self._rc_to_ow_user[rc_id] = u.id
            log.info('Bridge user map loaded (%d RC↔OW mappings)', len(self._rc_to_ow_user))
        except Exception as e:
            log.warning('Bridge could not build user map: %s', e)

    def register_user(self, rc_user_id: str, ow_user_id: str) -> None:
        """Called by rocketchat_sync after a new user is provisioned."""
        self._rc_to_ow_user[rc_user_id] = ow_user_id

    # ------------------------------------------------------------------
    # Subscription management — channels
    # ------------------------------------------------------------------

    async def subscribe_channel(self, room_id: str, channel_id: str) -> None:
        """Called by sync_channel_create after a room ID is assigned."""
        if not self._ddp or not self._ddp.connected:
            return
        self._room_to_channel[room_id] = channel_id
        sub_id = await self._ddp.subscribe(
            'stream-room-messages',
            [room_id, {'useCollection': False, 'args': [{'useRoles': True}]}],
            self._on_room_message,
        )
        self._room_to_sub[room_id] = sub_id
        log.debug('Bridge subscribed to RC room %s (channel %s)', room_id, channel_id)

    async def _subscribe_existing_channels(self) -> None:
        from open_webui.models.channels import Channels
        try:
            channels = await Channels.get_channels()
            for ch in channels:
                room_id = (ch.data or {}).get('rocketchat_room_id')
                if room_id:
                    await self.subscribe_channel(room_id, ch.id)
            log.info('Bridge loaded %d existing RC room subscriptions', len(self._room_to_sub))
        except Exception as e:
            log.warning('Bridge could not load existing channels: %s', e)

    # ------------------------------------------------------------------
    # Subscription management — presence (Phase 5)
    # ------------------------------------------------------------------

    async def _subscribe_presence(self) -> None:
        """Subscribe to the global user-status stream for online/away/offline events."""
        if not self._ddp or not self._ddp.connected:
            return
        await self._ddp.subscribe(
            'stream-notify-logged',
            ['user-status', {'useCollection': False, 'args': []}],
            self._on_presence_changed,
        )
        log.debug('Bridge subscribed to stream-notify-logged (user-status)')

    async def _on_presence_changed(self, data: dict) -> None:
        """
        Handles: stream-notify-logged / user-status
        Payload args: [[rc_user_id, username, status_code, statusText], ...]
        """
        if data.get('collection') != 'stream-notify-logged':
            return

        fields = data.get('fields') or {}
        if fields.get('eventName') != 'user-status':
            return

        for entry in fields.get('args') or []:
            if not isinstance(entry, list) or len(entry) < 3:
                continue
            rc_user_id, _username, status_code = entry[0], entry[1], entry[2]
            status_text = entry[3] if len(entry) > 3 else ''

            ow_user_id = self._rc_to_ow_user.get(rc_user_id)
            if not ow_user_id:
                continue

            presence = _RC_STATUS_MAP.get(status_code, 'offline')
            asyncio.create_task(self._apply_presence(ow_user_id, presence, status_text))

    async def _apply_presence(self, ow_user_id: str, presence: str, status_text: str) -> None:
        """Persist presence change in OW DB and broadcast to the user's Socket.IO room."""
        try:
            from open_webui.models.users import Users
            await Users.update_user_by_id(ow_user_id, {'presence_state': presence})

            from open_webui.socket.main import sio
            await sio.emit(
                'user:presence',
                {'user_id': ow_user_id, 'presence': presence, 'status_text': status_text},
                to=f'user:{ow_user_id}',
            )
        except Exception as e:
            log.warning('Bridge _apply_presence error for user %s: %s', ow_user_id, e)

    # ------------------------------------------------------------------
    # Subscription management — unread counts & badges (Phase 5.3)
    # ------------------------------------------------------------------

    async def _subscribe_unread_counts(self) -> None:
        """
        Subscribe to per-user notifications via stream-notify-user.

        RC's stream-notify-user fires events like:
          - "subscriptions-changed"  (unread count / badge updates)
          - "rooms-changed"          (last-message timestamps)
          - "notification"           (mention pings)

        We forward each event to the matching OW user's Socket.IO room so the
        sidebar badge updates in real time.
        """
        if not self._ddp or not self._ddp.connected:
            return

        # Subscribe individually for every OW user we know about. RC's notify-user
        # stream is keyed by rc_user_id/eventName, so we need one subscription
        # per (rc_user_id, eventName) pair.
        for rc_uid in list(self._rc_to_ow_user.keys()):
            for ev in ('subscriptions-changed', 'rooms-changed', 'notification'):
                try:
                    await self._ddp.subscribe(
                        'stream-notify-user',
                        [f'{rc_uid}/{ev}', {'useCollection': False, 'args': []}],
                        self._on_user_notification,
                    )
                except Exception as e:
                    log.debug('Bridge unread subscribe error (%s/%s): %s', rc_uid, ev, e)
        log.info('Bridge subscribed to stream-notify-user for %d users', len(self._rc_to_ow_user))

    async def _on_user_notification(self, data: dict) -> None:
        """Forward an RC user notification to the OW Socket.IO room of that user."""
        if data.get('collection') != 'stream-notify-user':
            return

        fields = data.get('fields') or {}
        event_name = fields.get('eventName') or ''
        # eventName is "<rc_user_id>/<event-type>"
        rc_user_id = event_name.split('/', 1)[0] if '/' in event_name else None
        if not rc_user_id:
            return
        ow_user_id = self._rc_to_ow_user.get(rc_user_id)
        if not ow_user_id:
            return

        args = fields.get('args') or []
        try:
            from open_webui.socket.main import sio
            # Always emit the raw RC payload — it carries unread/userMentions/etc.
            await sio.emit(
                'rocketchat:notification',
                {
                    'event': event_name.split('/', 1)[1] if '/' in event_name else event_name,
                    'args': args,
                },
                to=f'user:{ow_user_id}',
            )
        except Exception as e:
            log.warning('Bridge _on_user_notification emit error: %s', e)

    # ------------------------------------------------------------------
    # RC → OW (inbound messages)
    # ------------------------------------------------------------------

    async def _on_room_message(self, data: dict) -> None:
        # Defensive: only handle room-message stream frames shaped as expected.
        # The DDP client now routes by stream + eventName, but guard anyway so a
        # malformed frame (or a stray presence event) can never raise here.
        if data.get('collection') != 'stream-room-messages':
            return

        fields = data.get('fields') or {}
        args = fields.get('args') or []
        if not args or not isinstance(args[0], dict):
            return

        rc_msg = args[0]

        # Suppress echoes of messages we sent to RC
        if (rc_msg.get('customFields') or {}).get(_OW_ORIGIN_FIELD):
            return

        room_id = rc_msg.get('rid')
        channel_id = self._room_to_channel.get(room_id)
        if not channel_id:
            return

        content = (rc_msg.get('msg') or '').strip()
        if not content:
            return

        ow_user = await self._resolve_ow_user(rc_msg.get('u', {}))
        if ow_user is None:
            return

        message = await self._store_message(channel_id, ow_user.id, content, rc_msg.get('_id'))
        if message is None:
            return

        await self._emit_message(channel_id, message, ow_user)

    async def _resolve_ow_user(self, rc_user_info: dict):
        """
        Resolve an RC user dict to an Open WebUI UserModel.
        Tries: cached RC user ID map → email field → email lookup by RC API.
        """
        from open_webui.models.users import Users

        # Fast path: cached map
        rc_user_id = rc_user_info.get('_id')
        if rc_user_id:
            ow_user_id = self._rc_to_ow_user.get(rc_user_id)
            if ow_user_id:
                return await Users.get_user_by_id(ow_user_id)

        # Slow path: email embedded in the DDP payload
        for email_obj in rc_user_info.get('emails', []):
            address = email_obj.get('address', '')
            if address:
                return await Users.get_user_by_email(address)

        return None

    async def _store_message(self, channel_id: str, user_id: str, content: str, rc_msg_id: Optional[str]):
        from open_webui.models.messages import Messages, MessageForm
        try:
            form = MessageForm(
                content=content,
                data={'rocketchat_message_id': rc_msg_id} if rc_msg_id else None,
            )
            return await Messages.insert_new_message(form, channel_id, user_id)
        except Exception as e:
            log.warning('Bridge _store_message error: %s', e)
            return None

    async def _emit_message(self, channel_id: str, message, ow_user) -> None:
        try:
            from open_webui.socket.main import sio
            from open_webui.models.users import UserNameResponse

            await sio.emit(
                'events:channel',
                {
                    'channel_id': channel_id,
                    'message_id': message.id,
                    'data': {
                        'type': 'message',
                        'data': {'temp_id': None, **message.model_dump()},
                    },
                    'user': UserNameResponse(**ow_user.model_dump()).model_dump(),
                },
                to=f'channel:{channel_id}',
            )
        except Exception as e:
            log.warning('Bridge _emit_message error: %s', e)

    # ------------------------------------------------------------------
    # OW → RC (outbound messages)
    # ------------------------------------------------------------------

    async def forward_to_rc(
        self,
        channel_id: str,
        content: str,
        ow_message_id: str,
        files: Optional[list] = None,
    ) -> None:
        """
        Forward an Open WebUI message to Rocket.Chat.
        The ow_origin custom field prevents the DDP echo from being re-stored.
        Uses the REST API — independent of whether the DDP bridge is running.

        On success, the RC message ID is stored back on the OW message so
        later edits/pins/reactions can target the correct RC message.

        If ``files`` is provided (list of dicts with at least an ``id`` key
        pointing at OW file IDs), each file is uploaded into the RC room via
        rooms.upload after the text message is sent.
        """
        from open_webui.models.channels import Channels
        from open_webui.utils.rocketchat import get_client, is_configured

        if not is_configured():
            return

        channel = await Channels.get_channel_by_id(channel_id)
        room_id = (channel.data or {}).get('rocketchat_room_id') if channel else None
        if not room_id:
            return

        rc = get_client()
        rc_msg_id: Optional[str] = None

        # 1) text message (skipped when there's no body but files are present —
        #    rooms.upload itself takes a `msg` param to attach text to a file).
        if content:
            try:
                rc_msg = await rc.send_message(
                    room_id=room_id,
                    text=content,
                    custom_fields={_OW_ORIGIN_FIELD: True, 'ow_message_id': ow_message_id},
                )
                rc_msg_id = (rc_msg or {}).get('_id')
            except Exception as e:
                log.warning('Bridge forward_to_rc(text) failed for channel %s: %s', channel_id, e)

        # 2) Each attached file is forwarded to RC's rooms.upload so it
        #    appears as a real attachment in the RC client.
        if files:
            await self._forward_files_to_rc(rc, room_id, files, ow_message_id)

        # 3) Persist the RC message id on the OW message
        if rc_msg_id:
            try:
                from open_webui.models.messages import Messages
                existing = await Messages.get_message_by_id(ow_message_id)
                if existing:
                    merged = {
                        **(existing.data or {}),
                        'rocketchat_message_id': rc_msg_id,
                        'rocketchat_room_id': room_id,
                    }
                    from open_webui.internal.db import get_async_db_context
                    from open_webui.models.messages import Message
                    from sqlalchemy import update as sa_update
                    async with get_async_db_context() as db:
                        await db.execute(
                            sa_update(Message).where(Message.id == ow_message_id).values(data=merged)
                        )
                        await db.commit()
            except Exception as e:
                log.debug('forward_to_rc: could not persist rc_msg_id (%s)', e)

    async def _forward_files_to_rc(self, rc, room_id: str, files: list, ow_message_id: str) -> None:
        """
        Stream each Open WebUI file into Rocket.Chat's rooms.upload endpoint.
        Soft-fails per-file so one bad attachment cannot lose the whole message.
        """
        try:
            from open_webui.models.files import Files as FilesModel
            from open_webui.storage.provider import Storage
        except Exception as e:
            log.warning('Bridge _forward_files_to_rc imports failed: %s', e)
            return

        uploaded = 0
        for f in files:
            file_id = (f or {}).get('id') if isinstance(f, dict) else None
            if not file_id:
                continue
            try:
                file_row = await FilesModel.get_file_by_id(file_id)
                if not file_row or not getattr(file_row, 'path', None):
                    log.info('Bridge file forward skipped (no path) for OW file %s', file_id)
                    continue
                local_path = Storage.get_file(file_row.path)
                with open(local_path, 'rb') as fh:
                    content = fh.read()
                if not content:
                    log.info('Bridge file forward skipped (empty) for OW file %s', file_id)
                    continue
                ct = (
                    (file_row.meta or {}).get('content_type')
                    if file_row.meta else None
                ) or 'application/octet-stream'
                rc_msg = await rc.upload_to_room(
                    room_id=room_id,
                    filename=file_row.filename or 'file',
                    content=content,
                    content_type=ct,
                    description=f.get('description') if isinstance(f, dict) else None,
                )
                uploaded += 1
                log.info(
                    'Bridge file forward: OW file %s (%s, %d bytes) → RC room %s msg %s',
                    file_id, ct, len(content), room_id, (rc_msg or {}).get('_id'),
                )
            except Exception as exc:
                log.warning('Bridge _forward_files_to_rc: file %s failed: %s', file_id, exc)
        if files:
            log.info(
                'Bridge file forward complete for OW msg %s: %d/%d uploaded to RC room %s',
                ow_message_id, uploaded, len(files), room_id,
            )


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_bridge: Optional[RocketChatBridge] = None


def get_bridge() -> RocketChatBridge:
    global _bridge
    if _bridge is None:
        _bridge = RocketChatBridge()
    return _bridge
