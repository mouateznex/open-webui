"""
Rocket.Chat synchronisation handlers.

This module owns the *implementation* of every sync operation (create user,
push role change, archive channel, etc.).  Every public function is also
registered with ``rc_sync_queue`` so producer code can simply enqueue a job:

    from open_webui.utils import rc_sync_queue
    await rc_sync_queue.enqueue('user.role', {'user_id': user.id})

The queue layer takes care of persistence and retries; on a non-recoverable
error the handler must raise so the queue can back off.  Helpers prefixed
with ``_`` are internal and may swallow soft failures (e.g. "user already
gone") without raising.

Rocket.Chat user/room IDs are cached in OW JSON columns (`user.info`,
`channel.data`) so subsequent syncs don't pay another lookup round-trip.
"""

import logging
import re
import secrets
from typing import Any, Dict, Optional

from open_webui.models.users import UserModel, Users
from open_webui.models.channels import ChannelModel, Channels
from open_webui.utils.rocketchat import RocketChatError, get_client, is_configured
from open_webui.utils import rc_sync_queue

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


def _rc_channel_name(name: str) -> str:
    """Sanitise an Open WebUI channel name to a valid Rocket.Chat room name."""
    return re.sub(r'[^a-z0-9._-]', '-', (name or '').lower()).strip('-') or 'channel'


def _is_private(channel: ChannelModel) -> bool:
    return channel.type == 'group' or bool(getattr(channel, 'is_private', False))


# ---------------------------------------------------------------------------
# RC user-id resolution / caching
# ---------------------------------------------------------------------------


async def _get_rc_user_id(user: UserModel) -> Optional[str]:
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


async def _save_channel_data(channel_id: str, existing_data: Optional[dict], updates: dict) -> None:
    from open_webui.internal.db import get_async_db_context
    from open_webui.models.channels import Channel
    from sqlalchemy import update as sa_update

    merged = {**(existing_data or {}), **updates}
    async with get_async_db_context() as db:
        await db.execute(
            sa_update(Channel).where(Channel.id == channel_id).values(data=merged)
        )
        await db.commit()


# ---------------------------------------------------------------------------
# User sync handlers
# ---------------------------------------------------------------------------


async def ensure_user(user: UserModel) -> None:
    """Make sure a Rocket.Chat account exists for the given OW user with the right role."""
    if not is_configured() or user.role == 'pending':
        return

    rc = get_client()
    rc_user = await rc.get_user_by_email(user.email)

    if rc_user is None:
        new_user = await rc.create_user(
            email=user.email,
            name=user.name,
            username=_rc_username(user),
            password=secrets.token_urlsafe(32),
            roles=_rc_roles(user.role),
        )
        rc_id = new_user['_id']
        await _cache_rc_id(user.id, rc_id, user.info)

        try:
            from open_webui.utils.rocketchat_bridge import get_bridge
            get_bridge().register_user(rc_id, user.id)
        except Exception as e:
            log.debug('register_user side-effect skipped: %s', e)

        log.info('Rocket.Chat account provisioned for %s', user.email)
        return

    expected = _rc_roles(user.role)
    current = rc_user.get('roles', []) or []
    if sorted(expected) != sorted(current):
        await rc.set_user_roles(rc_user['_id'], expected)
        log.info('Rocket.Chat role corrected for %s: %s → %s', user.email, current, expected)
    if not (user.info or {}).get('rocketchat_user_id'):
        await _cache_rc_id(user.id, rc_user['_id'], user.info)


async def sync_user_role(user: UserModel) -> None:
    if not is_configured():
        return
    rc = get_client()
    rc_id = await _get_rc_user_id(user)
    if rc_id is None:
        # Provision first, then set role.
        await ensure_user(user)
        rc_id = await _get_rc_user_id(user)
        if rc_id is None:
            log.warning('sync_user_role: provisioning failed for %s', user.email)
            return
    await rc.set_user_roles(rc_id, _rc_roles(user.role))
    log.info('Rocket.Chat role synced for %s → %s', user.email, user.role)


async def sync_user_profile(user: UserModel) -> None:
    """Push name + email + avatar to Rocket.Chat."""
    if not is_configured():
        return
    rc = get_client()
    rc_id = await _get_rc_user_id(user)
    if rc_id is None:
        await ensure_user(user)
        rc_id = await _get_rc_user_id(user)
        if rc_id is None:
            log.warning('sync_user_profile: provisioning failed for %s', user.email)
            return

    update_data: dict = {'name': user.name, 'email': user.email}
    await rc.update_user(rc_id, update_data)

    # Avatar handled separately (different endpoint). Skip data-URI / base64.
    avatar = user.profile_image_url or ''
    if avatar and avatar.startswith(('http://', 'https://')):
        try:
            await rc.set_user_avatar(rc_id, avatar)
        except RocketChatError as e:
            # Some RC versions reject avatarUrl pointing at internal hostnames;
            # log and continue rather than re-queue.
            log.warning('Rocket.Chat avatar sync failed for %s: %s', user.email, e)
    log.info('Rocket.Chat profile synced for %s', user.email)


async def delete_user(user_id_snapshot: str, email: str, rc_user_id: Optional[str] = None) -> None:
    """The OW row may already be gone — we work from the snapshot."""
    if not is_configured():
        return
    rc = get_client()
    if not rc_user_id:
        rc_user = await rc.get_user_by_email(email)
        rc_user_id = (rc_user or {}).get('_id')
    if not rc_user_id:
        log.debug('delete_user: no RC account found for %s', email)
        return
    await rc.delete_user(rc_user_id)
    log.info('Rocket.Chat account deleted for %s', email)


async def set_user_active(user: UserModel, active: bool) -> None:
    if not is_configured():
        return
    rc = get_client()
    rc_id = await _get_rc_user_id(user)
    if rc_id is None:
        log.debug('set_user_active: no RC account for %s', user.email)
        return
    await rc.set_user_active(rc_id, active)


async def deactivate_user(user: UserModel) -> None:
    """Hard-deactivate (ban) — different from set_user_active(False)."""
    if not is_configured():
        return
    rc = get_client()
    rc_id = await _get_rc_user_id(user)
    if rc_id is None:
        return
    try:
        await rc.deactivate_user(rc_id)
    except RocketChatError:
        # Some RC versions don't expose users.deactivateIdle for individuals;
        # fall back to setActive(False) which has the same effective behaviour.
        await rc.set_user_active(rc_id, False)


async def sync_user_status(user: UserModel, status_message: Optional[str] = None) -> None:
    if not is_configured():
        return
    rc = get_client()
    rc_id = await _get_rc_user_id(user)
    if rc_id is None:
        return
    rc_status = {
        'online': 'online',
        'away': 'away',
        'busy': 'busy',
        'offline': 'offline',
    }.get(user.presence_state or '', 'online')
    await rc.set_user_status(rc_id, rc_status, message=status_message)


async def sync_user_preferences(user: UserModel, preferences: dict) -> None:
    """Mirror OW notification settings into Rocket.Chat user preferences."""
    if not is_configured():
        return
    rc = get_client()
    rc_id = await _get_rc_user_id(user)
    if rc_id is None:
        return
    await rc.set_user_preferences(rc_id, preferences)


async def register_push_token(user: UserModel, app_name: str, token: str, platform: str) -> None:
    if not is_configured():
        return
    rc = get_client()
    rc_id = await _get_rc_user_id(user)
    if rc_id is None:
        return
    await rc.push_register_token(rc_id, app_name, token, platform)


# ---------------------------------------------------------------------------
# Channel sync handlers
# ---------------------------------------------------------------------------


async def sync_channel_create(channel: ChannelModel) -> None:
    """Create the matching Rocket.Chat room and persist the room ID."""
    if not is_configured():
        return

    rc = get_client()
    rc_name = _rc_channel_name(channel.name)

    # DMs go through the dedicated DM path
    if channel.type == 'dm':
        await sync_dm_channel(channel)
        return

    if _is_private(channel):
        room = await rc.create_group(rc_name)
    else:
        room = await rc.create_channel(rc_name)

    room_id = room.get('_id')
    if not room_id:
        raise RocketChatError(0, 'no roomId returned from RC')

    await _save_rc_room_id(channel.id, channel.data, room_id)

    if channel.description:
        if _is_private(channel):
            await rc.set_group_description(room_id, channel.description)
        else:
            await rc.set_channel_description(room_id, channel.description)

    try:
        from open_webui.utils.rocketchat_bridge import get_bridge
        await get_bridge().subscribe_channel(room_id, channel.id)
    except Exception as e:
        log.debug('bridge subscription skipped during create: %s', e)

    log.info('Rocket.Chat room created for channel "%s" (roomId=%s)', channel.name, room_id)


async def sync_channel_update(channel: ChannelModel, old_name: Optional[str] = None) -> None:
    if not is_configured() or channel.type == 'dm':
        return

    room_id = _get_rc_room_id(channel)
    if not room_id:
        await sync_channel_create(channel)
        return

    rc = get_client()
    private = _is_private(channel)
    rc_name = _rc_channel_name(channel.name)

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


async def sync_channel_delete_by_snapshot(
    rc_room_id: Optional[str],
    private: bool,
    name: Optional[str] = None,
) -> None:
    if not is_configured():
        return
    if not rc_room_id:
        log.debug('sync_channel_delete: no rc_room_id snapshot for "%s"', name)
        return
    rc = get_client()
    if private:
        await rc.delete_group(rc_room_id)
    else:
        await rc.delete_channel(rc_room_id)
    log.info('Rocket.Chat room deleted (roomId=%s, name=%s)', rc_room_id, name)


# ---------------------------------------------------------------------------
# Channel feature sync (Phase 3.2)
# ---------------------------------------------------------------------------


async def sync_channel_topic(channel: ChannelModel, topic: str) -> None:
    if not is_configured() or channel.type == 'dm':
        return
    room_id = _get_rc_room_id(channel)
    if not room_id:
        await sync_channel_create(channel)
        room_id = _get_rc_room_id(await Channels.get_channel_by_id(channel.id))
        if not room_id:
            return
    rc = get_client()
    if _is_private(channel):
        await rc.set_group_topic(room_id, topic)
    else:
        await rc.set_channel_topic(room_id, topic)
    await _save_channel_data(channel.id, channel.data, {'topic': topic})


async def sync_channel_announcement(channel: ChannelModel, announcement: str) -> None:
    if not is_configured() or channel.type == 'dm':
        return
    room_id = _get_rc_room_id(channel)
    if not room_id:
        return
    rc = get_client()
    if _is_private(channel):
        await rc.set_group_announcement(room_id, announcement)
    else:
        await rc.set_channel_announcement(room_id, announcement)
    await _save_channel_data(channel.id, channel.data, {'announcement': announcement})


async def sync_channel_read_only(channel: ChannelModel, read_only: bool) -> None:
    if not is_configured() or channel.type == 'dm':
        return
    room_id = _get_rc_room_id(channel)
    if not room_id:
        return
    rc = get_client()
    if _is_private(channel):
        await rc.set_group_read_only(room_id, read_only)
    else:
        await rc.set_channel_read_only(room_id, read_only)
    await _save_channel_data(channel.id, channel.data, {'read_only': read_only})


async def sync_channel_archive(channel: ChannelModel, archived: bool) -> None:
    if not is_configured() or channel.type == 'dm':
        return
    room_id = _get_rc_room_id(channel)
    if not room_id:
        return
    rc = get_client()
    if _is_private(channel):
        if archived:
            await rc.archive_group(room_id)
        else:
            await rc.unarchive_group(room_id)
    else:
        if archived:
            await rc.archive_channel(room_id)
        else:
            await rc.unarchive_channel(room_id)
    await _save_channel_data(channel.id, channel.data, {'archived': archived})


async def sync_channel_join_code(channel: ChannelModel, join_code: str) -> None:
    if not is_configured() or channel.type == 'dm' or _is_private(channel):
        return
    room_id = _get_rc_room_id(channel)
    if not room_id:
        return
    rc = get_client()
    await rc.set_channel_join_code(room_id, join_code or '')
    await _save_channel_data(channel.id, channel.data, {'join_code_set': bool(join_code)})


async def sync_channel_default(channel: ChannelModel, default: bool) -> None:
    if not is_configured() or channel.type == 'dm' or _is_private(channel):
        return
    room_id = _get_rc_room_id(channel)
    if not room_id:
        return
    rc = get_client()
    await rc.set_channel_default(room_id, default)
    await _save_channel_data(channel.id, channel.data, {'is_default': default})


async def sync_channel_role(
    channel: ChannelModel,
    user: UserModel,
    role: str,                  # 'owner' | 'moderator' | 'leader'
    action: str = 'add',         # 'add' | 'remove'
) -> None:
    if not is_configured() or channel.type == 'dm':
        return
    room_id = _get_rc_room_id(channel)
    if not room_id:
        return
    rc = get_client()
    rc_user_id = await _get_rc_user_id(user)
    if not rc_user_id:
        return
    private = _is_private(channel)
    handler_table = {
        ('add', 'owner', False): rc.add_channel_owner,
        ('add', 'owner', True): rc.add_group_owner,
        ('remove', 'owner', False): rc.remove_channel_owner,
        ('remove', 'owner', True): rc.add_group_owner,        # RC has no remove-owner for groups
        ('add', 'moderator', False): rc.add_channel_moderator,
        ('add', 'moderator', True): rc.add_group_moderator,
        ('remove', 'moderator', False): rc.remove_channel_moderator,
        ('add', 'leader', False): rc.add_channel_leader,
        ('add', 'leader', True): rc.add_group_leader,
        ('remove', 'leader', False): rc.remove_channel_leader,
    }
    func = handler_table.get((action, role, private))
    if func is None:
        log.warning('sync_channel_role: unsupported (%s, %s, private=%s)', action, role, private)
        return
    await func(room_id, rc_user_id)


async def sync_channel_member(
    channel: ChannelModel,
    user: UserModel,
    action: str = 'add',  # 'add' | 'remove'
) -> None:
    if not is_configured() or channel.type == 'dm':
        return
    room_id = _get_rc_room_id(channel)
    if not room_id:
        return
    rc = get_client()
    rc_user_id = await _get_rc_user_id(user)
    if not rc_user_id:
        return
    private = _is_private(channel)
    if action == 'add':
        if private:
            await rc.invite_to_group(room_id, rc_user_id)
        else:
            await rc.invite_to_channel(room_id, rc_user_id)
    elif action == 'remove':
        if private:
            await rc.kick_from_group(room_id, rc_user_id)
        else:
            await rc.kick_from_channel(room_id, rc_user_id)


# ---------------------------------------------------------------------------
# DM sync (Phase 3.3) — DMs were previously skipped
# ---------------------------------------------------------------------------


async def sync_dm_channel(channel: ChannelModel) -> None:
    """
    Create the matching Rocket.Chat DM room. Both OW members must already
    have RC accounts; we resolve via the cached RC user IDs.
    """
    if not is_configured() or channel.type != 'dm':
        return

    room_id = _get_rc_room_id(channel)
    if room_id:
        return  # already provisioned

    rc = get_client()
    members = await Channels.get_members_by_channel_id(channel.id)
    usernames: list = []
    for m in members:
        u = await Users.get_user_by_id(m.user_id)
        if not u:
            continue
        rc_user = (u.info or {}).get('rocketchat_user_id')
        rc_data = await rc.get_user_by_id(rc_user) if rc_user else await rc.get_user_by_email(u.email)
        if rc_data and rc_data.get('username'):
            usernames.append(rc_data['username'])

    if len(usernames) < 2:
        log.debug('sync_dm_channel: not enough RC users to create DM (%d)', len(usernames))
        return

    room = await rc.create_dm(usernames)
    room_id = room.get('rid') or room.get('_id')
    if not room_id:
        raise RocketChatError(0, 'no roomId returned from im.create')

    await _save_rc_room_id(channel.id, channel.data, room_id)

    try:
        from open_webui.utils.rocketchat_bridge import get_bridge
        await get_bridge().subscribe_channel(room_id, channel.id)
    except Exception:
        pass

    log.info('Rocket.Chat DM provisioned for channel %s (roomId=%s)', channel.id, room_id)


# ---------------------------------------------------------------------------
# Queue handler registration
# ---------------------------------------------------------------------------


async def _resolve_user(payload: Dict[str, Any]) -> Optional[UserModel]:
    user_id = payload.get('user_id')
    if not user_id:
        return None
    return await Users.get_user_by_id(user_id)


async def _resolve_channel(payload: Dict[str, Any]) -> Optional[ChannelModel]:
    channel_id = payload.get('channel_id')
    if not channel_id:
        return None
    return await Channels.get_channel_by_id(channel_id)


# Each handler must raise on failure so the queue retries; on "user gone"
# style soft conditions they return silently because there is nothing to do.


async def _h_user_ensure(payload):
    user = await _resolve_user(payload)
    if user:
        await ensure_user(user)


async def _h_user_role(payload):
    user = await _resolve_user(payload)
    if user:
        await sync_user_role(user)


async def _h_user_profile(payload):
    user = await _resolve_user(payload)
    if user:
        await sync_user_profile(user)


async def _h_user_delete(payload):
    await delete_user(
        user_id_snapshot=payload.get('user_id', ''),
        email=payload.get('email', ''),
        rc_user_id=payload.get('rc_user_id'),
    )


async def _h_user_active(payload):
    user = await _resolve_user(payload)
    if user:
        await set_user_active(user, bool(payload.get('active', True)))


async def _h_user_deactivate(payload):
    user = await _resolve_user(payload)
    if user:
        await deactivate_user(user)


async def _h_user_status(payload):
    user = await _resolve_user(payload)
    if user:
        await sync_user_status(user, status_message=payload.get('status_message'))


async def _h_user_preferences(payload):
    user = await _resolve_user(payload)
    if user:
        await sync_user_preferences(user, payload.get('preferences') or {})


async def _h_user_push_register(payload):
    user = await _resolve_user(payload)
    if user:
        await register_push_token(
            user,
            app_name=payload.get('app_name', 'open-webui'),
            token=payload['token'],
            platform=payload.get('platform', 'gcm'),
        )


async def _h_channel_create(payload):
    channel = await _resolve_channel(payload)
    if channel:
        await sync_channel_create(channel)


async def _h_channel_update(payload):
    channel = await _resolve_channel(payload)
    if channel:
        await sync_channel_update(channel, old_name=payload.get('old_name'))


async def _h_channel_delete(payload):
    await sync_channel_delete_by_snapshot(
        rc_room_id=payload.get('rc_room_id'),
        private=bool(payload.get('private')),
        name=payload.get('name'),
    )


async def _h_channel_topic(payload):
    channel = await _resolve_channel(payload)
    if channel:
        await sync_channel_topic(channel, payload.get('topic') or '')


async def _h_channel_announcement(payload):
    channel = await _resolve_channel(payload)
    if channel:
        await sync_channel_announcement(channel, payload.get('announcement') or '')


async def _h_channel_read_only(payload):
    channel = await _resolve_channel(payload)
    if channel:
        await sync_channel_read_only(channel, bool(payload.get('read_only')))


async def _h_channel_archive(payload):
    channel = await _resolve_channel(payload)
    if channel:
        await sync_channel_archive(channel, bool(payload.get('archived')))


async def _h_channel_join_code(payload):
    channel = await _resolve_channel(payload)
    if channel:
        await sync_channel_join_code(channel, payload.get('join_code') or '')


async def _h_channel_default(payload):
    channel = await _resolve_channel(payload)
    if channel:
        await sync_channel_default(channel, bool(payload.get('default')))


async def _h_channel_role(payload):
    channel = await _resolve_channel(payload)
    user = await _resolve_user(payload)
    if channel and user:
        await sync_channel_role(
            channel, user,
            role=payload.get('role', 'moderator'),
            action=payload.get('action', 'add'),
        )


async def _h_channel_member(payload):
    channel = await _resolve_channel(payload)
    user = await _resolve_user(payload)
    if channel and user:
        await sync_channel_member(channel, user, action=payload.get('action', 'add'))


async def _h_dm_create(payload):
    channel = await _resolve_channel(payload)
    if channel:
        await sync_dm_channel(channel)


# Register everything at import time. The queue itself is started later
# from main.py lifespan.
def _register_handlers() -> None:
    rc_sync_queue.register('user.ensure', _h_user_ensure)
    rc_sync_queue.register('user.role', _h_user_role)
    rc_sync_queue.register('user.profile', _h_user_profile)
    rc_sync_queue.register('user.delete', _h_user_delete)
    rc_sync_queue.register('user.active', _h_user_active)
    rc_sync_queue.register('user.deactivate', _h_user_deactivate)
    rc_sync_queue.register('user.status', _h_user_status)
    rc_sync_queue.register('user.preferences', _h_user_preferences)
    rc_sync_queue.register('user.push_register', _h_user_push_register)

    rc_sync_queue.register('channel.create', _h_channel_create)
    rc_sync_queue.register('channel.update', _h_channel_update)
    rc_sync_queue.register('channel.delete', _h_channel_delete)
    rc_sync_queue.register('channel.topic', _h_channel_topic)
    rc_sync_queue.register('channel.announcement', _h_channel_announcement)
    rc_sync_queue.register('channel.read_only', _h_channel_read_only)
    rc_sync_queue.register('channel.archive', _h_channel_archive)
    rc_sync_queue.register('channel.join_code', _h_channel_join_code)
    rc_sync_queue.register('channel.default', _h_channel_default)
    rc_sync_queue.register('channel.role', _h_channel_role)
    rc_sync_queue.register('channel.member', _h_channel_member)
    rc_sync_queue.register('dm.create', _h_dm_create)


_register_handlers()
