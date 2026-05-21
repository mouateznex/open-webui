import json
import logging
import secrets
import time
from typing import Optional
from urllib.parse import urlencode

import asyncio

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from open_webui.env import REDIS_KEY_PREFIX
from open_webui.models.users import Users
from open_webui.utils.auth import create_token, decode_token

log = logging.getLogger(__name__)

router = APIRouter()

# Authorization code TTL: 10 minutes
_CODE_TTL = 600
# Access token TTL: 1 hour
_TOKEN_TTL = 3600

# In-memory auth code store used when Redis is not configured.
# { code: { user_id, client_id, redirect_uri, scope, expires_at } }
_codes: dict = {}


def _clients(request: Request) -> dict:
    return getattr(request.app.state, 'oauth_server_clients', {})


def _validate_client(
    request: Request,
    client_id: str,
    client_secret: Optional[str] = None,
    redirect_uri: Optional[str] = None,
) -> bool:
    client = _clients(request).get(client_id)
    if not client:
        return False
    if client_secret is not None and not secrets.compare_digest(
        client.get('client_secret', ''), client_secret
    ):
        return False
    if redirect_uri is not None and redirect_uri not in client.get('redirect_uris', []):
        return False
    return True


async def _store_code(request: Request, code: str, payload: dict) -> None:
    if request.app.state.redis:
        await request.app.state.redis.set(
            f'{REDIS_KEY_PREFIX}:oauth_server:code:{code}',
            json.dumps(payload),
            ex=_CODE_TTL,
        )
    else:
        _codes[code] = payload
        now = int(time.time())
        for k in [k for k, v in list(_codes.items()) if v.get('expires_at', 0) < now]:
            _codes.pop(k, None)


async def _consume_code(request: Request, code: str) -> Optional[dict]:
    if request.app.state.redis:
        key = f'{REDIS_KEY_PREFIX}:oauth_server:code:{code}'
        raw = await request.app.state.redis.get(key)
        if not raw:
            return None
        await request.app.state.redis.delete(key)
        return json.loads(raw)
    else:
        payload = _codes.pop(code, None)
        if payload is None:
            return None
        if payload.get('expires_at', 0) < int(time.time()):
            return None
        return payload


# ---------------------------------------------------------------------------
# OIDC Discovery
# ---------------------------------------------------------------------------


@router.get('/.well-known/openid-configuration', include_in_schema=False)
async def oidc_discovery(request: Request):
    base = str(request.app.state.config.WEBUI_URL).rstrip('/')
    return JSONResponse({
        'issuer': base,
        'authorization_endpoint': f'{base}/oauth/authorize',
        'token_endpoint': f'{base}/oauth/token',
        'userinfo_endpoint': f'{base}/oauth/userinfo',
        'response_types_supported': ['code'],
        'subject_types_supported': ['public'],
        'id_token_signing_alg_values_supported': ['HS256'],
        'scopes_supported': ['openid', 'profile', 'email'],
        'token_endpoint_auth_methods_supported': ['client_secret_post'],
        'claims_supported': ['sub', 'email', 'name', 'picture', 'roles'],
    })


# ---------------------------------------------------------------------------
# Authorization endpoint
# ---------------------------------------------------------------------------


@router.get('/oauth/authorize')
async def authorize(
    request: Request,
    client_id: str,
    redirect_uri: str,
    response_type: str = 'code',
    state: Optional[str] = None,
    scope: Optional[str] = 'openid',
):
    if response_type != 'code':
        raise HTTPException(status_code=400, detail='Only response_type=code is supported')

    if not _validate_client(request, client_id, redirect_uri=redirect_uri):
        raise HTTPException(status_code=400, detail='Invalid client_id or redirect_uri')

    # Check for an active Open WebUI session cookie
    user = None
    token = request.cookies.get('token')
    if token:
        data = decode_token(token)
        if data and 'id' in data:
            user = await Users.get_user_by_id(data['id'])

    # Pending users are not allowed through
    if user is None or user.role == 'pending':
        params = urlencode({
            k: v for k, v in {
                'client_id': client_id,
                'redirect_uri': redirect_uri,
                'response_type': response_type,
                'state': state or '',
                'scope': scope or 'openid',
            }.items() if v
        })
        # Redirect to Open WebUI login; after login the frontend will
        # follow the `redirect` param back to this endpoint.
        # The whole inner URL (path + query) MUST be URL-encoded as a single
        # value, otherwise its `?`/`&` separators leak into /auth's own query
        # string and the inner client_id/redirect_uri/state params are lost.
        inner_url = f'/oauth/authorize?{params}'
        login_query = urlencode({'redirect': inner_url})
        return RedirectResponse(url=f'/auth?{login_query}')

    # Issue a single-use authorization code
    code = secrets.token_urlsafe(32)
    await _store_code(request, code, {
        'user_id': user.id,
        'client_id': client_id,
        'redirect_uri': redirect_uri,
        'scope': scope or 'openid',
        'expires_at': int(time.time()) + _CODE_TTL,
    })

    params = {'code': code}
    if state:
        params['state'] = state
    return RedirectResponse(url=f'{redirect_uri}?{urlencode(params)}')


# ---------------------------------------------------------------------------
# Token endpoint
# ---------------------------------------------------------------------------


@router.post('/oauth/token')
async def token(
    request: Request,
    grant_type: str = Form(...),
    code: str = Form(...),
    redirect_uri: str = Form(...),
    client_id: str = Form(...),
    client_secret: str = Form(...),
):
    if grant_type != 'authorization_code':
        raise HTTPException(status_code=400, detail='Only grant_type=authorization_code is supported')

    if not _validate_client(request, client_id, client_secret=client_secret, redirect_uri=redirect_uri):
        raise HTTPException(status_code=401, detail='Invalid client credentials')

    payload = await _consume_code(request, code)
    if not payload:
        raise HTTPException(status_code=400, detail='Invalid or expired authorization code')

    if payload['client_id'] != client_id or payload['redirect_uri'] != redirect_uri:
        raise HTTPException(status_code=400, detail='Code was not issued for this client')

    from datetime import timedelta
    access_token = create_token(
        {'id': payload['user_id'], 'scope': payload['scope']},
        expires_delta=timedelta(seconds=_TOKEN_TTL),
    )

    return JSONResponse({
        'access_token': access_token,
        'token_type': 'Bearer',
        'expires_in': _TOKEN_TTL,
        'scope': payload['scope'],
    })


# ---------------------------------------------------------------------------
# Userinfo endpoint
# ---------------------------------------------------------------------------


@router.get('/oauth/userinfo')
async def userinfo(request: Request):
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer '):
        raise HTTPException(status_code=401, detail='Missing Bearer token')

    data = decode_token(auth_header[len('Bearer '):])
    if not data or 'id' not in data:
        raise HTTPException(status_code=401, detail='Invalid token')

    user = await Users.get_user_by_id(data['id'])
    if not user:
        raise HTTPException(status_code=401, detail='User not found')

    if user.role == 'pending':
        raise HTTPException(status_code=403, detail='Account pending approval')

    # Fire-and-forget: provision/correct the Rocket.Chat account in the background
    # so the HTTP response is not delayed by the RC API call.
    from open_webui.utils.rocketchat_sync import ensure_user
    asyncio.create_task(ensure_user(user))

    return JSONResponse({
        'sub': user.id,
        'email': user.email,
        'name': user.name,
        'picture': user.profile_image_url or '',
        # Rocket.Chat reads this claim to assign roles.
        # 'admin' maps to Rocket.Chat admin; 'user' maps to regular user.
        'roles': [user.role],
    })
