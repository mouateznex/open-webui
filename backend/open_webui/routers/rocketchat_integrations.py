"""
Rocket.Chat Integrations — Outgoing Webhook / Slash Command handler.

When Rocket.Chat is configured with an outgoing webhook that has a trigger
word (e.g. "ask"), it POSTs to POST /rocketchat/slash every time a user
sends a message beginning with that word.  This endpoint calls the
configured AI model and returns {"text": "..."} so RC posts the reply in
the channel automatically.

RC Admin setup (Admin → Integrations → New Outgoing Webhook):
  Event trigger : Message Sent
  Trigger words : ask        (or any word you prefer)
  URLs          : http://<open-webui-host>/rocketchat/slash
  Token         : <value of ROCKETCHAT_SLASH_TOKEN>

Environment variables:
  ROCKETCHAT_SLASH_TOKEN  — shared secret; RC sends it in every POST
  ROCKETCHAT_SLASH_MODEL  — model ID to use (empty = app default model)
"""

import json
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict

from open_webui.env import ROCKETCHAT_SLASH_TOKEN, ROCKETCHAT_SLASH_MODEL
from open_webui.models.users import UserModel

log = logging.getLogger(__name__)
router = APIRouter()


class RCSlashPayload(BaseModel):
    model_config = ConfigDict(extra='ignore')

    token: str
    channel_id: str = ''
    channel_name: str = ''
    user_id: str = ''
    user_name: str = ''
    text: str = ''
    trigger_word: str = ''


def _extract_query(text: str, trigger_word: str) -> str:
    if trigger_word and text.lower().startswith(trigger_word.lower()):
        return text[len(trigger_word):].strip()
    return text.strip()


def _pick_model(request: Request, preferred: str) -> Optional[str]:
    models: dict = getattr(request.app.state, 'MODELS', {})
    if not models:
        return None
    if preferred and preferred in models:
        return preferred
    default_csv = (getattr(request.app.state.config, 'DEFAULT_MODELS', None) or '').strip()
    for candidate in default_csv.split(','):
        candidate = candidate.strip()
        if candidate and candidate in models:
            return candidate
    return next(iter(models), None)


@router.post('/rocketchat/slash')
async def rc_slash_handler(request: Request, payload: RCSlashPayload):
    """
    Outgoing webhook entry point for Rocket.Chat.
    Returns {"text": "<AI reply>"} — RC posts it to the originating channel.
    Returns {} on misconfiguration to avoid RC error popups.
    """
    if not ROCKETCHAT_SLASH_TOKEN:
        log.warning('RC slash endpoint hit but ROCKETCHAT_SLASH_TOKEN is not configured')
        return {}

    if payload.token != ROCKETCHAT_SLASH_TOKEN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Invalid webhook token')

    query = _extract_query(payload.text, payload.trigger_word)
    if not query:
        return {'text': '⚠️ Please provide a query after the trigger word. Example: `ask What is Python?`'}

    model_id = _pick_model(request, ROCKETCHAT_SLASH_MODEL)
    if not model_id:
        log.warning('RC slash: no AI model available in app state')
        return {'text': '⚠️ No AI model is currently configured in Open WebUI.'}

    # Minimal admin-role user object — bypasses model access control
    bot_user = UserModel.model_construct(
        id='rocketchat-slash-bot',
        role='admin',
        name='RC Slash Bot',
        email='',
        username='rc-slash-bot',
    )

    try:
        from open_webui.utils.chat import generate_chat_completion

        response = await generate_chat_completion(
            request,
            {
                'model': model_id,
                'messages': [{'role': 'user', 'content': query}],
                'stream': False,
            },
            bot_user,
            bypass_filter=True,
        )

        body = json.loads(response.body)
        answer = body['choices'][0]['message']['content']
        log.info('RC slash replied to "%s" (%d chars)', query[:60], len(answer))
        return {'text': f'🤖 **AI:** {answer}'}

    except Exception as e:
        log.warning('RC slash AI completion error: %s', e)
        return {'text': f'⚠️ AI error — {e}'}
