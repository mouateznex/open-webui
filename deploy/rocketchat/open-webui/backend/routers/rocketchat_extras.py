"""
Rocket.Chat REST surface for Open WebUI.

This router exposes everything that the plan calls for but that wasn't
already covered by ``channels.py`` / ``users.py`` / ``oauth_server.py``:

- Teams (Phase 3.4)
- Message parity: threads, reactions, starred, pinned, URL previews,
  mention notification routing (Phase 4.2)
- File / media / audio upload forwarding to rooms.upload (Phase 4.3, 9.2)
- Presence & subscriptions for unread counts/badges (Phase 5.3)
- Omnichannel / LiveChat surface (Phase 3.2)
- Admin audit log surfacing (Phase 6.3)
- Global federated search across OW + RC (Phase 7.2)
- Incoming webhook management (Phase 8.1)
- Marketplace / Apps Engine surfacing (Phase 8.2)
- AI bot posting into RC rooms (Phase 8.3)

Every endpoint short-circuits gracefully when Rocket.Chat is not configured.
The slash command handler from ``rocketchat_integrations.py`` continues to
exist; this file just adds *broader* surfaces around it.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from pydantic import BaseModel

from open_webui.constants import ERROR_MESSAGES
from open_webui.models.users import Users, UserModel
from open_webui.models.channels import Channels
from open_webui.models.messages import Messages, MessageForm
from open_webui.utils.auth import get_admin_user, get_verified_user
from open_webui.utils.rocketchat import RocketChatError, get_client, is_configured

log = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_rc():
    if not is_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='Rocket.Chat is not configured',
        )
    return get_client()


async def _resolve_rc_room_id(channel_id: str) -> Optional[str]:
    channel = await Channels.get_channel_by_id(channel_id)
    if not channel:
        return None
    return (channel.data or {}).get('rocketchat_room_id')


async def _resolve_rc_user_id(user: UserModel) -> Optional[str]:
    cached = (user.info or {}).get('rocketchat_user_id') if user.info else None
    if cached:
        return cached
    rc = get_client()
    rc_user = await rc.get_user_by_email(user.email)
    if rc_user:
        return rc_user.get('_id')
    return None


# ===========================================================================
# Teams (Phase 3.4)
# ===========================================================================


teams_router = APIRouter()


@teams_router.get('/', response_model=list)
async def list_teams(user=Depends(get_verified_user), count: int = 100, offset: int = 0):
    if not is_configured():
        return []
    return await get_client().list_teams(count=count, offset=offset)


class TeamCreateForm(BaseModel):
    name: str
    private: bool = False
    members: list[str] = []  # OW user IDs


@teams_router.post('/create')
async def create_team(form_data: TeamCreateForm, user=Depends(get_verified_user)):
    rc = _require_rc()

    rc_owner_id = await _resolve_rc_user_id(user)

    rc_members: list = []
    for uid in form_data.members:
        u = await Users.get_user_by_id(uid)
        if not u:
            continue
        rc_user = await rc.get_user_by_email(u.email)
        if rc_user and rc_user.get('username'):
            rc_members.append(rc_user['username'])

    team = await rc.create_team(
        name=form_data.name,
        members=rc_members,
        team_type=1 if form_data.private else 0,
        owner=rc_owner_id,
    )
    return team


@teams_router.delete('/{team_id}', response_model=bool)
async def delete_team(team_id: str, user=Depends(get_admin_user)):
    rc = _require_rc()
    return await rc.delete_team(team_id)


@teams_router.get('/{team_id}/rooms', response_model=list)
async def list_team_rooms(team_id: str, user=Depends(get_verified_user)):
    rc = _require_rc()
    return await rc.list_team_rooms(team_id)


class TeamMemberForm(BaseModel):
    user_ids: list[str]
    roles: list[str] = []  # 'owner' | 'admin' | 'member'


@teams_router.post('/{team_id}/members/add')
async def add_team_members(team_id: str, form_data: TeamMemberForm, user=Depends(get_verified_user)):
    rc = _require_rc()
    rc_members = []
    for uid in form_data.user_ids:
        u = await Users.get_user_by_id(uid)
        if not u:
            continue
        rc_id = await _resolve_rc_user_id(u)
        if rc_id:
            rc_members.append({'userId': rc_id, 'roles': form_data.roles or ['member']})
    return await rc.add_team_members(team_id, rc_members)


@teams_router.post('/{team_id}/members/{user_id}/remove', response_model=bool)
async def remove_team_member(team_id: str, user_id: str, user=Depends(get_verified_user)):
    rc = _require_rc()
    target = await Users.get_user_by_id(user_id)
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.USER_NOT_FOUND)
    rc_id = await _resolve_rc_user_id(target)
    if not rc_id:
        return False
    return await rc.remove_team_member(team_id, rc_id)


# ===========================================================================
# Message parity (Phase 4.2)
# ===========================================================================


messages_router = APIRouter()


@messages_router.get('/{channel_id}/threads')
async def list_room_threads(channel_id: str, user=Depends(get_verified_user), count: int = 50, offset: int = 0):
    rc = _require_rc()
    rid = await _resolve_rc_room_id(channel_id)
    if not rid:
        return []
    return await rc.get_thread_list(rid, count=count, offset=offset)


@messages_router.get('/threads/{thread_id}')
async def get_thread_messages_rc(thread_id: str, user=Depends(get_verified_user), count: int = 50, offset: int = 0):
    rc = _require_rc()
    return await rc.get_thread_messages(thread_id, count=count, offset=offset)


@messages_router.get('/{channel_id}/starred')
async def get_starred_messages(channel_id: str, user=Depends(get_verified_user), count: int = 50, offset: int = 0):
    rc = _require_rc()
    rid = await _resolve_rc_room_id(channel_id)
    if not rid:
        return []
    return await rc.get_starred_messages(rid, count=count, offset=offset)


@messages_router.get('/{channel_id}/pinned')
async def get_pinned_messages_rc(channel_id: str, user=Depends(get_verified_user), count: int = 50, offset: int = 0):
    rc = _require_rc()
    rid = await _resolve_rc_room_id(channel_id)
    if not rid:
        return []
    return await rc.get_pinned_messages(rid, count=count, offset=offset)


class StarMessageForm(BaseModel):
    message_id: str        # OW or RC message id (we resolve)
    starred: bool = True


@messages_router.post('/star', response_model=bool)
async def star_message(form_data: StarMessageForm, user=Depends(get_verified_user)):
    rc = _require_rc()

    rc_msg_id = form_data.message_id
    ow_msg = await Messages.get_message_by_id(form_data.message_id)
    if ow_msg:
        rc_msg_id = (ow_msg.data or {}).get('rocketchat_message_id') or rc_msg_id

    if form_data.starred:
        await rc.star_message(rc_msg_id)
    else:
        await rc.unstar_message(rc_msg_id)
    return True


class PinMessageRCForm(BaseModel):
    message_id: str
    pinned: bool = True


@messages_router.post('/pin', response_model=bool)
async def pin_message_rc(form_data: PinMessageRCForm, user=Depends(get_verified_user)):
    rc = _require_rc()

    rc_msg_id = form_data.message_id
    ow_msg = await Messages.get_message_by_id(form_data.message_id)
    if ow_msg:
        rc_msg_id = (ow_msg.data or {}).get('rocketchat_message_id') or rc_msg_id

    if form_data.pinned:
        await rc.pin_message(rc_msg_id)
    else:
        await rc.unpin_message(rc_msg_id)
    return True


class ReactMessageRCForm(BaseModel):
    message_id: str
    emoji: str
    react: bool = True


@messages_router.post('/react', response_model=bool)
async def react_message_rc(form_data: ReactMessageRCForm, user=Depends(get_verified_user)):
    rc = _require_rc()
    rc_msg_id = form_data.message_id
    ow_msg = await Messages.get_message_by_id(form_data.message_id)
    if ow_msg:
        rc_msg_id = (ow_msg.data or {}).get('rocketchat_message_id') or rc_msg_id
    emoji = form_data.emoji
    if not emoji.startswith(':'):
        emoji = f':{emoji.strip(":")}:'
    await rc.react_to_message(rc_msg_id, emoji, should_react=form_data.react)
    return True


@messages_router.get('/{channel_id}/mentions')
async def list_channel_mentions(channel_id: str, user=Depends(get_verified_user), count: int = 50, offset: int = 0):
    """Mention notification routing — returns mentions of any user in this room."""
    rc = _require_rc()
    rid = await _resolve_rc_room_id(channel_id)
    if not rid:
        return []
    return await rc.get_mentions(rid, count=count, offset=offset)


class URLPreviewForm(BaseModel):
    channel_id: str
    url: str


@messages_router.post('/url-preview')
async def get_url_preview(form_data: URLPreviewForm, user=Depends(get_verified_user)):
    rc = _require_rc()
    rid = await _resolve_rc_room_id(form_data.channel_id)
    if not rid:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Channel not bridged to RC')
    return await rc.get_room_url_preview(rid, form_data.url)


# ===========================================================================
# File / media / audio upload (Phase 4.3 + 9.2)
# ===========================================================================


files_router = APIRouter()


@files_router.post('/{channel_id}/upload')
async def upload_to_room(
    channel_id: str,
    file: UploadFile = File(...),
    description: Optional[str] = Form(None),
    msg: Optional[str] = Form(None),
    thread_id: Optional[str] = Form(None),
    user=Depends(get_verified_user),
):
    """
    Forward a file (image, audio, video, anything) into a Rocket.Chat room
    using rooms.upload. The same endpoint handles audio messages — the
    client just sets the appropriate Content-Type.
    """
    rc = _require_rc()
    rid = await _resolve_rc_room_id(channel_id)
    if not rid:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Channel not bridged to RC')

    content = await file.read()
    if not content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Empty file')

    # OW imposes its own size limit elsewhere; the RC server has its own setting.
    rc_msg = await rc.upload_to_room(
        room_id=rid,
        filename=file.filename or 'file',
        content=content,
        content_type=file.content_type or 'application/octet-stream',
        description=description,
        msg=msg,
        thread_id=thread_id,
    )
    return {'rc_message': rc_msg, 'rc_message_id': rc_msg.get('_id'), 'room_id': rid}


@files_router.post('/{channel_id}/upload/audio')
async def upload_audio_to_room(
    channel_id: str,
    file: UploadFile = File(...),
    description: Optional[str] = Form('Audio message'),
    user=Depends(get_verified_user),
):
    """
    Convenience wrapper for audio messages — same as /upload but with a
    sensible default description so RC renders the bubble correctly.
    """
    rc = _require_rc()
    rid = await _resolve_rc_room_id(channel_id)
    if not rid:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Channel not bridged to RC')

    content = await file.read()
    if not content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Empty audio')

    if not (file.content_type or '').startswith('audio/'):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail='Content-Type must be audio/*',
        )

    rc_msg = await rc.upload_to_room(
        room_id=rid,
        filename=file.filename or 'recording.webm',
        content=content,
        content_type=file.content_type,
        description=description,
    )
    return {'rc_message_id': rc_msg.get('_id'), 'room_id': rid}


# ===========================================================================
# Presence + unread counts/badges (Phase 5.1, 5.3)
# ===========================================================================


presence_router = APIRouter()


@presence_router.get('/me')
async def my_presence(user=Depends(get_verified_user)):
    rc = _require_rc()
    rc_id = await _resolve_rc_user_id(user)
    if not rc_id:
        return {'status': 'offline', 'message': '', 'connectionStatus': 'offline'}
    return await rc.get_user_presence(rc_id)


@presence_router.get('/{user_id}')
async def user_presence(user_id: str, user=Depends(get_verified_user)):
    rc = _require_rc()
    target = await Users.get_user_by_id(user_id)
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=ERROR_MESSAGES.USER_NOT_FOUND)
    rc_id = await _resolve_rc_user_id(target)
    if not rc_id:
        return {'status': 'offline'}
    return await rc.get_user_presence(rc_id)


@presence_router.get('/subscriptions/unread')
async def my_unread_subscriptions(user=Depends(get_verified_user)):
    """
    Aggregate unread counts/badges from RC's subscription stream. Mirrors
    what stream-notify-user emits in real time but exposes a one-shot HTTP
    fallback that the frontend can use on first paint or when the socket
    is reconnecting.
    """
    rc = _require_rc()
    subs = await rc.get_subscriptions()
    return [
        {
            'rid': s.get('rid'),
            'name': s.get('name'),
            'unread': s.get('unread', 0),
            'user_mentions': s.get('userMentions', 0),
            'group_mentions': s.get('groupMentions', 0),
            'last_message_ts': (s.get('ls') or {}).get('$date') if isinstance(s.get('ls'), dict) else s.get('ls'),
            'open': s.get('open', False),
            'alert': s.get('alert', False),
        }
        for s in subs
    ]


class MarkReadForm(BaseModel):
    channel_id: Optional[str] = None
    room_id: Optional[str] = None


@presence_router.post('/subscriptions/read', response_model=bool)
async def mark_subscription_read(form_data: MarkReadForm, user=Depends(get_verified_user)):
    rc = _require_rc()
    rid = form_data.room_id
    if not rid and form_data.channel_id:
        rid = await _resolve_rc_room_id(form_data.channel_id)
    if not rid:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='room_id or channel_id required')
    return await rc.mark_room_read(rid)


# ===========================================================================
# Omnichannel / LiveChat (Phase 3.2)
# ===========================================================================


omnichannel_router = APIRouter()


@omnichannel_router.get('/departments')
async def list_departments(user=Depends(get_verified_user)):
    return await _require_rc().list_livechat_departments() if is_configured() else []


@omnichannel_router.get('/rooms')
async def list_omni_rooms(user=Depends(get_verified_user), count: int = 50, offset: int = 0):
    return await _require_rc().list_livechat_rooms(count=count, offset=offset) if is_configured() else []


@omnichannel_router.get('/agents')
async def list_omni_agents(user=Depends(get_verified_user)):
    return await _require_rc().list_livechat_agents() if is_configured() else []


class AgentForm(BaseModel):
    username: str


@omnichannel_router.post('/agents/add')
async def add_omni_agent(form_data: AgentForm, user=Depends(get_admin_user)):
    return await _require_rc().add_livechat_agent(form_data.username)


@omnichannel_router.post('/agents/remove', response_model=bool)
async def remove_omni_agent(form_data: AgentForm, user=Depends(get_admin_user)):
    return await _require_rc().remove_livechat_agent(form_data.username)


class TakeRoomForm(BaseModel):
    room_id: str
    agent_username: str


@omnichannel_router.post('/rooms/take', response_model=bool)
async def take_omni_room(form_data: TakeRoomForm, user=Depends(get_verified_user)):
    return await _require_rc().take_livechat_room(form_data.room_id, form_data.agent_username)


class CloseRoomForm(BaseModel):
    room_id: str
    comment: str = ''


@omnichannel_router.post('/rooms/close', response_model=bool)
async def close_omni_room(form_data: CloseRoomForm, user=Depends(get_verified_user)):
    return await _require_rc().close_livechat_room(form_data.room_id, form_data.comment)


# ===========================================================================
# Audit logs (Phase 6.3)
# ===========================================================================


audit_router = APIRouter()


@audit_router.get('/')
async def list_audit_logs(
    user=Depends(get_admin_user),
    start: Optional[int] = None,
    end: Optional[int] = None,
    rc_user: Optional[str] = None,
    count: int = 100,
):
    if not is_configured():
        return []
    return await get_client().get_audit_logs(start=start, end=end, user=rc_user, count=count)


@audit_router.get('/messages')
async def list_message_audit_logs(
    user=Depends(get_admin_user),
    room_id: Optional[str] = None,
    count: int = 100,
):
    if not is_configured():
        return []
    return await get_client().get_message_audit_logs(room_id=room_id, count=count)


# ===========================================================================
# Global federated search (Phase 7.2)
# ===========================================================================


search_router = APIRouter()


@search_router.get('/')
async def global_search(
    q: str = Query(..., min_length=1),
    limit: int = 50,
    user=Depends(get_verified_user),
):
    """
    Federated search across BOTH Open WebUI's local data and Rocket.Chat.

    Aggregates:
    - Open WebUI channel messages (via Messages.search_messages_by_channel_ids)
    - Rocket.Chat spotlight (rooms + users) and chat.search results
    """
    query = q.strip()

    out: dict = {'query': query, 'open_webui': [], 'rocketchat': {}}

    # Open WebUI: search OW channel messages the user can see.
    try:
        from open_webui.models.channels import Channels
        my_channels = await Channels.get_channels_by_user_id(user.id)
        ids = [c.id for c in my_channels]
        if ids:
            local = await Messages.search_messages_by_channel_ids(ids, query, limit=limit)
            out['open_webui'] = [
                {
                    'id': m.id,
                    'channel_id': m.channel_id,
                    'user_id': m.user_id,
                    'content': m.content,
                    'created_at': m.created_at,
                }
                for m in local
            ]
    except Exception as e:
        log.warning('OW global search failed: %s', e)

    # Rocket.Chat: spotlight (rooms + users) + chat.search
    try:
        if is_configured():
            rc = get_client()
            spotlight = await rc.search_global(query, count=limit)
            out['rocketchat'] = spotlight[0] if spotlight else {}
    except Exception as e:
        log.warning('RC global search failed: %s', e)

    return out


# ===========================================================================
# Incoming webhook management (Phase 8.1)
# ===========================================================================


webhooks_router = APIRouter()


@webhooks_router.get('/integrations')
async def list_rc_integrations(user=Depends(get_admin_user), count: int = 100, offset: int = 0):
    if not is_configured():
        return []
    return await get_client().list_integrations(count=count, offset=offset)


class IncomingWebhookForm(BaseModel):
    name: str
    channel: str            # '#general' or 'roomId' style
    username: str = 'rocket.cat'
    emoji: Optional[str] = None
    avatar: Optional[str] = None
    enabled: bool = True


@webhooks_router.post('/incoming/create')
async def create_incoming_webhook(form_data: IncomingWebhookForm, user=Depends(get_admin_user)):
    rc = _require_rc()
    return await rc.create_incoming_webhook(
        name=form_data.name,
        channel=form_data.channel,
        username=form_data.username,
        emoji=form_data.emoji,
        avatar=form_data.avatar,
        enabled=form_data.enabled,
    )


class OutgoingWebhookForm(BaseModel):
    name: str
    urls: list[str]
    channel: str
    trigger_words: list[str] = []
    username: Optional[str] = None
    token: Optional[str] = None
    enabled: bool = True


@webhooks_router.post('/outgoing/create')
async def create_outgoing_webhook(form_data: OutgoingWebhookForm, user=Depends(get_admin_user)):
    rc = _require_rc()
    return await rc.create_outgoing_webhook(
        name=form_data.name,
        urls=form_data.urls,
        channel=form_data.channel,
        trigger_words=form_data.trigger_words,
        username=form_data.username,
        token=form_data.token,
        enabled=form_data.enabled,
    )


@webhooks_router.delete('/{integration_id}', response_model=bool)
async def delete_rc_integration(
    integration_id: str,
    user=Depends(get_admin_user),
    integration_type: str = 'webhook-incoming',
):
    rc = _require_rc()
    return await rc.delete_integration(integration_id, integration_type=integration_type)


class PostToWebhookForm(BaseModel):
    webhook_url: str
    payload: dict


@webhooks_router.post('/post', response_model=dict)
async def post_to_webhook(form_data: PostToWebhookForm, user=Depends(get_verified_user)):
    """Forward an arbitrary payload to one of RC's incoming webhook URLs."""
    rc = _require_rc()
    return await rc.post_to_incoming_webhook(form_data.webhook_url, form_data.payload)


# ===========================================================================
# Marketplace / Apps Engine (Phase 8.2)
# ===========================================================================


apps_router = APIRouter()


@apps_router.get('/installed')
async def list_installed_apps(user=Depends(get_admin_user)):
    if not is_configured():
        return []
    return await get_client().list_apps()


@apps_router.get('/marketplace')
async def list_marketplace(user=Depends(get_admin_user)):
    if not is_configured():
        return []
    return await get_client().list_marketplace_apps()


@apps_router.get('/{app_id}')
async def get_app(app_id: str, user=Depends(get_admin_user)):
    if not is_configured():
        return {}
    return await get_client().get_app(app_id) or {}


@apps_router.get('/commands/list')
async def list_slash_commands(user=Depends(get_verified_user)):
    if not is_configured():
        return []
    return await get_client().list_slash_commands()


class SlashRunForm(BaseModel):
    channel_id: str
    command: str
    params: str = ''


@apps_router.post('/commands/run')
async def run_slash_command(form_data: SlashRunForm, user=Depends(get_verified_user)):
    rc = _require_rc()
    rid = await _resolve_rc_room_id(form_data.channel_id)
    if not rid:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Channel not bridged to RC')
    return await rc.run_slash_command(rid, form_data.command, form_data.params)


# ===========================================================================
# Bot / AI posting into RC rooms (Phase 8.3)
# ===========================================================================


bot_router = APIRouter()


class BotPostForm(BaseModel):
    channel_id: Optional[str] = None
    room_id: Optional[str] = None
    text: str
    alias: str = 'AI'
    avatar: Optional[str] = None
    emoji: Optional[str] = ':robot_face:'
    thread_id: Optional[str] = None


@bot_router.post('/post')
async def bot_post_message(form_data: BotPostForm, user=Depends(get_verified_user)):
    """
    Post an AI/bot reply into a Rocket.Chat room. The message appears in
    both interfaces simultaneously (via the OW DDP bridge).
    """
    rc = _require_rc()
    rid = form_data.room_id
    if not rid and form_data.channel_id:
        rid = await _resolve_rc_room_id(form_data.channel_id)
    if not rid:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='room_id or channel_id required')

    msg = await rc.post_message_as(
        room_id=rid,
        text=form_data.text,
        alias=form_data.alias,
        avatar=form_data.avatar,
        emoji=form_data.emoji,
    )
    return {'rc_message_id': msg.get('_id'), 'room_id': rid}


class BotAIInferForm(BaseModel):
    channel_id: Optional[str] = None
    room_id: Optional[str] = None
    prompt: str
    model: Optional[str] = None
    alias: str = 'AI'
    emoji: str = ':robot_face:'


@bot_router.post('/ai')
async def bot_ai_post_message(request: Request, form_data: BotAIInferForm, user=Depends(get_verified_user)):
    """
    Run the prompt through an AI model and post the answer back into the room.
    This is the broader AI-bot endpoint — distinct from the narrow outgoing-
    webhook /rocketchat/slash entry point.
    """
    import json

    rc = _require_rc()
    rid = form_data.room_id
    if not rid and form_data.channel_id:
        rid = await _resolve_rc_room_id(form_data.channel_id)
    if not rid:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='room_id or channel_id required')

    models: dict = getattr(request.app.state, 'MODELS', {}) or {}
    model_id = form_data.model
    if not model_id or model_id not in models:
        from open_webui.env import ROCKETCHAT_SLASH_MODEL
        if ROCKETCHAT_SLASH_MODEL and ROCKETCHAT_SLASH_MODEL in models:
            model_id = ROCKETCHAT_SLASH_MODEL
        else:
            default_csv = (getattr(request.app.state.config, 'DEFAULT_MODELS', None) or '').strip()
            for cand in default_csv.split(','):
                cand = cand.strip()
                if cand and cand in models:
                    model_id = cand
                    break
            if not model_id:
                model_id = next(iter(models), None)

    if not model_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='No AI model is available')

    try:
        from open_webui.utils.chat import generate_chat_completion

        response = await generate_chat_completion(
            request,
            {
                'model': model_id,
                'messages': [{'role': 'user', 'content': form_data.prompt}],
                'stream': False,
            },
            user,
            bypass_filter=False,
        )
        try:
            body = json.loads(response.body)
        except Exception:
            body = response if isinstance(response, dict) else {}
        answer = body.get('choices', [{}])[0].get('message', {}).get('content') if isinstance(body, dict) else None
        if not answer:
            answer = '(no AI response)'
    except HTTPException:
        raise
    except Exception as e:
        log.warning('bot_ai_post_message: AI inference error: %s', e)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f'AI inference failed: {e}')

    msg = await rc.post_message_as(
        room_id=rid,
        text=answer,
        alias=form_data.alias,
        emoji=form_data.emoji,
    )
    return {'rc_message_id': msg.get('_id'), 'room_id': rid, 'answer': answer}


# ===========================================================================
# Mount sub-routers
# ===========================================================================


router.include_router(messages_router, prefix='/rocketchat/messages', tags=['rocketchat-messages'])
router.include_router(files_router, prefix='/rocketchat/files', tags=['rocketchat-files'])
router.include_router(presence_router, prefix='/rocketchat/presence', tags=['rocketchat-presence'])
router.include_router(omnichannel_router, prefix='/rocketchat/omnichannel', tags=['rocketchat-omnichannel'])
router.include_router(audit_router, prefix='/rocketchat/audit', tags=['rocketchat-audit'])
router.include_router(search_router, prefix='/search', tags=['search'])
router.include_router(webhooks_router, prefix='/rocketchat/webhooks', tags=['rocketchat-webhooks'])
router.include_router(apps_router, prefix='/rocketchat/apps', tags=['rocketchat-apps'])
router.include_router(bot_router, prefix='/rocketchat/bot', tags=['rocketchat-bot'])
router.include_router(teams_router, prefix='/teams', tags=['teams'])
