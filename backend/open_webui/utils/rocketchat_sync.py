"""
Rocket.Chat user synchronisation helpers.

Each function is safe to call as a FastAPI BackgroundTask — they silently
log errors rather than raising, so a Rocket.Chat outage never breaks the
Open WebUI request that triggered the sync.

Rocket.Chat user IDs are cached in the Open WebUI user's `info` JSON column
under the key ``rocketchat_user_id`` so repeated lookups against the
Rocket.Chat API are avoided.
"""

import logging
import re
import secrets
import time
from typing import Optional

from open_webui.models.users import UserModel, Users
from open_webui.models.channels import ChannelModel
from open_webui.utils.rocketchat import RocketChatError, get_client, is_configured

log = logging.getLogger(__name__)

# Role mapping: Open WebUI role → Rocket.Chat roles list
_ROLE_MAP = {
    'admin': ['admin', 'user'],
    'user': ['user'],
}


def _rc_username(user: UserModel) -> str:
    """Derive a Rocket.Chat-safe username from the Open WebUI user."""
    base = user.username or user.email.split('@')[0]
    return re.sub(r'[^a-z0-9._-]', '.', base.lower()).strip('.')


def _rc_roles(owui_role: str) -> list:
    return _ROLE_MAP.get(owui_role, ['user'])


async def _get_rc_user_id(user: UserModel) -> Optional[str]:
    """
    Return the cached Rocket.Chat user ID from the Open WebUI info column,
    or look it up by email and cache it if not yet stored.
    """
    cached = (user.info or {}).get('rocketchat_user_id')
    if cached:
        return cached

    rc = get_client()
    rc_user = await rc.get_user_by_email(user.email)
    if rc_user:
        rc_id = rc_user['_id']
        await _cache_rc_id(user.id, rc_id, user.info)
        return rc_id
    return None


async def _cache_rc_id(owui_user_id: str, rc_user_id: str, existing_info: Optional[dict]) -> None:
    merged = {**(existing_info or {}), 'rocketchat_user_id': rc_user_id}
    await Users.update_user_by_id(owui_user_id, {'info': merged})


# ---------------------------------------------------------------------------
# Public sync functions
# ---------------------------------------------------------------------------


async def ensure_user(user: UserModel) -> None:
    """
    Called on every successful Open WebUI login (via /oauth/userinfo).
    Creates the Rocket.Chat account if it doesn't exist, and corrects the
    role if it has drifted out of sync.
    """
    if not is_configured():
        return
    if user.role == 'pending':
        return

    try:
        rc = get_client()
        rc_user = await rc.get_user_by_email(user.email)

        if rc_user is None:
            # First login — provision the account
            new_user = await rc.create_user(
                email=user.email,
                name=user.name,
                username=_rc_username(user),
                # Password is random and never exposed; auth is via OAuth only
                password=secrets.token_urlsafe(32),
                roles=_rc_roles(user.role),
            )
            await _cache_rc_id(user.id, new_user['_id'], user.info)
            log.info('Rocket.Chat account provisioned for %s', user.email)
        else:
            # Account exists — ensure role is correct
            expected = _rc_roles(user.role)
            current = rc_user.get('roles', [])
            if sorted(expected) != sorted(current):
                await rc.set_user_roles(rc_user['_id'], expected)
                log.info('Rocket.Chat role corrected for %s: %s → %s', user.email, current, expected)
            # Cache the ID if we didn't have it yet
            if not (user.info or {}).get('rocketchat_user_id'):
                await _cache_rc_id(user.id, rc_user['_id'], user.info)

    except RocketChatError as e:
        log.warning('Rocket.Chat ensure_user failed for %s: %s', user.email, e)
    except Exception as e:
        log.warning('Rocket.Chat ensure_user unexpected error for %s: %s', user.email, e)


async def sync_user_role(user: UserModel) -> None:
    """
    Called immediately when an admin changes a user's role in Open WebUI.
    Pushes the new role to Rocket.Chat without waiting for next login.
    """
    if not is_configured():
        return

    try:
        rc = get_client()
        rc_id = await _get_rc_user_id(user)
        if rc_id is None:
            log.debug('Rocket.Chat sync_user_role: no RC account found for %s, skipping', user.email)
            return
        await rc.set_user_roles(rc_id, _rc_roles(user.role))
        log.info('Rocket.Chat role synced for %s → %s', user.email, user.role)

    except RocketChatError as e:
        log.warning('Rocket.Chat sync_user_role failed for %s: %s', user.email, e)
    except Exception as e:
        log.warning('Rocket.Chat sync_user_role unexpected error for %s: %s', user.email, e)


async def sync_user_profile(user: UserModel) -> None:
    """
    Called when a user updates their name or email in Open WebUI.
    Pushes name, email, and avatar URL to Rocket.Chat.
    """
    if not is_configured():
        return

    try:
        rc = get_client()
        rc_id = await _get_rc_user_id(user)
        if rc_id is None:
            log.debug('Rocket.Chat sync_user_profile: no RC account found for %s, skipping', user.email)
            return

        update_data: dict = {'name': user.name, 'email': user.email}
        if user.profile_image_url and user.profile_image_url.startswith('http'):
            update_data['avatarUrl'] = user.profile_image_url

        await rc.update_user(rc_id, update_data)
        log.info('Rocket.Chat profile synced for %s', user.email)

    except RocketChatError as e:
        log.warning('Rocket.Chat sync_user_profile failed for %s: %s', user.email, e)
    except Exception as e:
        log.warning('Rocket.Chat sync_user_profile unexpected error for %s: %s', user.email, e)


async def delete_user(user: UserModel) -> None:
    """
    Called when a user is deleted from Open WebUI.
    Removes the corresponding Rocket.Chat account.
    """
    if not is_configured():
        return

    try:
        rc = get_client()
        rc_id = await _get_rc_user_id(user)
        if rc_id is None:
            log.debug('Rocket.Chat delete_user: no RC account found for %s, skipping', user.email)
            return
        await rc.delete_user(rc_id)
        log.info('Rocket.Chat account deleted for %s', user.email)

    except RocketChatError as e:
        log.warning('Rocket.Chat delete_user failed for %s: %s', user.email, e)
    except Exception as e:
        log.warning('Rocket.Chat delete_user unexpected error for %s: %s', user.email, e)


# ---------------------------------------------------------------------------
# Channel sync helpers
# ---------------------------------------------------------------------------

# DM channels are intentionally excluded — they map 1-to-1 with users and
# are provisioned on demand by the real-time bridge (Phase 4).
_SKIP_TYPES = {'dm'}


def _rc_channel_name(name: str) -> str:
    """Sanitise an Open WebUI channel name to a valid Rocket.Chat room name."""
    return re.sub(r'[^a-z0-9._-]', '-', name.lower()).strip('-') or 'channel'


def _is_private(channel: ChannelModel) -> bool:
    return channel.type == 'group' or bool(channel.is_private)


def _get_rc_room_id(channel: ChannelModel) -> Optional[str]:
    return (channel.data or {}).get('rocketchat_room_id')


async def _save_rc_room_id(channel_id: str, existing_data: Optional[dict], room_id: str) -> None:
    """Persist the Rocket.Chat room ID into channel.data without overwriting other keys."""
    from open_webui.internal.db import get_async_db_context
    from open_webui.models.channels import Channel
    from sqlalchemy import update as sa_update

    merged = {**(existing_data or {}), 'rocketchat_room_id': room_id}
    async with get_async_db_context() as db:
        await db.execute(
            sa_update(Channel).where(Channel.id == channel_id).values(data=merged)
        )
        await db.commit()


async def sync_channel_create(channel: ChannelModel) -> None:
    """
    Called after a new Open WebUI channel is created.
    Creates the matching room in Rocket.Chat and stores the room ID in
    channel.data so future update/delete calls can find it.
    DM channels are skipped.
    """
    if not is_configured() or channel.type in _SKIP_TYPES:
        return

    try:
        rc = get_client()
        rc_name = _rc_channel_name(channel.name)

        if _is_private(channel):
            room = await rc.create_group(rc_name)
        else:
            room = await rc.create_channel(rc_name)

        room_id = room.get('_id')
        if not room_id:
            log.warning('Rocket.Chat sync_channel_create: no room ID returned for %s', channel.name)
            return

        # Persist the room ID so update/delete calls can reference it
        await _save_rc_room_id(channel.id, channel.data, room_id)

        # Sync description if provided
        if channel.description:
            if _is_private(channel):
                await rc.set_group_description(room_id, channel.description)
            else:
                await rc.set_channel_description(room_id, channel.description)

        log.info('Rocket.Chat room created for channel "%s" (roomId=%s)', channel.name, room_id)

    except RocketChatError as e:
        log.warning('Rocket.Chat sync_channel_create failed for "%s": %s', channel.name, e)
    except Exception as e:
        log.warning('Rocket.Chat sync_channel_create unexpected error for "%s": %s', channel.name, e)


async def sync_channel_update(channel: ChannelModel, old_name: Optional[str] = None) -> None:
    """
    Called after an Open WebUI channel is updated.
    Syncs name and description changes to the Rocket.Chat room.
    """
    if not is_configured() or channel.type in _SKIP_TYPES:
        return

    room_id = _get_rc_room_id(channel)
    if not room_id:
        # Room was never synced — create it now
        await sync_channel_create(channel)
        return

    try:
        rc = get_client()
        private = _is_private(channel)
        rc_name = _rc_channel_name(channel.name)

        # Only call rename if the name actually changed
        if old_name and _rc_channel_name(old_name) != rc_name:
            if private:
                await rc.rename_group(room_id, rc_name)
            else:
                await rc.rename_channel(room_id, rc_name)

        if channel.description is not None:
            if private:
                await rc.set_group_description(room_id, channel.description or '')
            else:
                await rc.set_channel_description(room_id, channel.description or '')

        log.info('Rocket.Chat room updated for channel "%s"', channel.name)

    except RocketChatError as e:
        log.warning('Rocket.Chat sync_channel_update failed for "%s": %s', channel.name, e)
    except Exception as e:
        log.warning('Rocket.Chat sync_channel_update unexpected error for "%s": %s', channel.name, e)


async def sync_channel_delete(channel: ChannelModel) -> None:
    """
    Called after an Open WebUI channel is deleted.
    Deletes the corresponding Rocket.Chat room.
    """
    if not is_configured() or channel.type in _SKIP_TYPES:
        return

    room_id = _get_rc_room_id(channel)
    if not room_id:
        log.debug('Rocket.Chat sync_channel_delete: no room ID for "%s", skipping', channel.name)
        return

    try:
        rc = get_client()
        if _is_private(channel):
            await rc.delete_group(room_id)
        else:
            await rc.delete_channel(room_id)

        log.info('Rocket.Chat room deleted for channel "%s" (roomId=%s)', channel.name, room_id)

    except RocketChatError as e:
        log.warning('Rocket.Chat sync_channel_delete failed for "%s": %s', channel.name, e)
    except Exception as e:
        log.warning('Rocket.Chat sync_channel_delete unexpected error for "%s": %s', channel.name, e)
