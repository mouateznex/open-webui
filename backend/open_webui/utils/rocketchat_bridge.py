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

Subscription management
  subscribe_channel() is called by the channel router every time a new Open
  WebUI channel gets a Rocket.Chat room ID assigned.
  At bridge start, all existing channels are loaded and subscribed in bulk.
"""

import asyncio
import logging
from typing import Optional

log = logging.getLogger(__name__)

# RC → OW: messages that carried ow_origin=True are echoes from our own REST
# posts.  We suppress them in the DDP handler using this custom field rather
# than a TTL set, which is simpler and perfectly reliable.
_OW_ORIGIN_FIELD = 'ow_origin'


class RocketChatBridge:
    def __init__(self):
        self._ddp = None
        # Maps RC room ID → Open WebUI channel ID
        self._room_to_channel: dict[str, str] = {}
        # Maps RC room ID → DDP subscription ID (for unsubscribe)
        self._room_to_sub: dict[str, str] = {}
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

        await self._subscribe_existing_channels()
        self._running = True
        log.info('Rocket.Chat real-time bridge running')

    async def stop(self) -> None:
        self._running = False
        if self._ddp:
            await self._ddp.close()

    # ------------------------------------------------------------------
    # Subscription management
    # ------------------------------------------------------------------

    async def subscribe_channel(self, room_id: str, channel_id: str) -> None:
        """Called by the channel router after sync_channel_create assigns a room ID."""
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
        """On bridge start, subscribe to all channels that already have a room ID."""
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
    # RC → OW (inbound)
    # ------------------------------------------------------------------

    async def _on_room_message(self, data: dict) -> None:
        fields = data.get('fields', {})
        args = fields.get('args', [])
        if not args:
            return

        rc_msg = args[0]

        # Suppress echoes of messages we sent to RC
        if (rc_msg.get('customFields') or {}).get(_OW_ORIGIN_FIELD):
            return

        room_id = rc_msg.get('rid')
        channel_id = self._room_to_channel.get(room_id)
        if not channel_id:
            return

        content = rc_msg.get('msg', '').strip()
        if not content:
            return

        # Resolve the RC user to an Open WebUI user
        rc_user_info = rc_msg.get('u', {})
        ow_user = await self._resolve_ow_user(rc_user_info)
        if ow_user is None:
            return

        # Persist in Open WebUI
        message = await self._store_message(channel_id, ow_user.id, content, rc_msg.get('_id'))
        if message is None:
            return

        # Broadcast to connected browsers via Socket.IO
        await self._emit_message(channel_id, message, ow_user)

    async def _resolve_ow_user(self, rc_user_info: dict):
        """Look up the Open WebUI user by the email stored in the RC user record.
        RC's stream-room-messages includes the `emails` array on the `u` object
        only if the user has one; fall back to username-based lookup otherwise."""
        from open_webui.models.users import Users

        emails = rc_user_info.get('emails', [])
        if emails:
            email = emails[0].get('address', '')
            if email:
                return await Users.get_user_by_email(email)

        # Fallback: look up by username as email prefix (imprecise but useful
        # when email is not present in the DDP payload)
        username = rc_user_info.get('username', '')
        if username:
            return await Users.get_user_by_username(username)

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
    # OW → RC (outbound)
    # ------------------------------------------------------------------

    async def forward_to_rc(self, channel_id: str, content: str, ow_message_id: str) -> None:
        """
        Forward an Open WebUI message to Rocket.Chat.
        Called by the channel router after post_new_message succeeds.
        The ow_origin custom field prevents the DDP echo from being re-stored.
        """
        if not self._running:
            return

        from open_webui.models.channels import Channels
        from open_webui.utils.rocketchat import get_client, is_configured

        if not is_configured():
            return

        channel = await Channels.get_channel_by_id(channel_id)
        room_id = (channel.data or {}).get('rocketchat_room_id') if channel else None
        if not room_id:
            return

        try:
            rc = get_client()
            await rc.send_message(
                room_id=room_id,
                text=content,
                custom_fields={_OW_ORIGIN_FIELD: True, 'ow_message_id': ow_message_id},
            )
        except Exception as e:
            log.warning('Bridge forward_to_rc failed for channel %s: %s', channel_id, e)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_bridge: Optional[RocketChatBridge] = None


def get_bridge() -> RocketChatBridge:
    global _bridge
    if _bridge is None:
        _bridge = RocketChatBridge()
    return _bridge
