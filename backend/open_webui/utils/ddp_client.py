"""
Minimal async DDP (Distributed Data Protocol) WebSocket client.

DDP is the protocol used by Rocket.Chat (Meteor.js) for real-time
communication. This client handles:
  - Connection handshake
  - SHA-256 password login
  - Method calls (login, etc.)
  - Pub/sub subscriptions (stream-room-messages)
  - Automatic ping/pong keepalive

Only the subset of DDP needed for the Open WebUI bridge is implemented.
"""

import asyncio
import hashlib
import json
import logging
import uuid
from typing import Callable, Optional

import aiohttp

log = logging.getLogger(__name__)


class DDPError(Exception):
    pass


class DDPClient:
    def __init__(self, url: str):
        # url must be the ws:// or wss:// WebSocket endpoint, e.g.
        # ws://rocketchat:3000/websocket
        self._url = url
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._session: Optional[aiohttp.ClientSession] = None

        # Pending method calls: call_id → Future
        self._pending: dict[str, asyncio.Future] = {}

        # Active subscriptions: sub_id → async callback(data)
        self._subscriptions: dict[str, Callable] = {}

        self._listener_task: Optional[asyncio.Task] = None
        self.connected = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        self._session = aiohttp.ClientSession()
        self._ws = await self._session.ws_connect(self._url)
        await self._send({'msg': 'connect', 'version': '1', 'support': ['1']})

        # Wait for the server's 'connected' acknowledgement
        async for raw in self._ws:
            if raw.type != aiohttp.WSMsgType.TEXT:
                continue
            msg = json.loads(raw.data)
            if msg.get('msg') == 'connected':
                break
            if msg.get('msg') == 'failed':
                raise DDPError(f'DDP version negotiation failed: {msg}')

        self.connected = True
        self._listener_task = asyncio.create_task(self._listen())
        log.debug('DDP connected to %s', self._url)

    async def login(self, username: str, password: str) -> dict:
        digest = hashlib.sha256(password.encode()).hexdigest()
        result = await self._call('login', [{
            'user': {'username': username},
            'password': {'digest': digest, 'algorithm': 'sha-256'},
        }])
        log.debug('DDP logged in as %s', username)
        return result

    async def subscribe(self, name: str, params: list, callback: Callable) -> str:
        """Subscribe to a Rocket.Chat stream. Returns the subscription ID."""
        sub_id = str(uuid.uuid4())
        self._subscriptions[sub_id] = callback
        await self._send({'msg': 'sub', 'id': sub_id, 'name': name, 'params': params})
        return sub_id

    async def unsubscribe(self, sub_id: str) -> None:
        self._subscriptions.pop(sub_id, None)
        await self._send({'msg': 'unsub', 'id': sub_id})

    async def close(self) -> None:
        self.connected = False
        if self._listener_task:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass
        if self._ws and not self._ws.closed:
            await self._ws.close()
        if self._session and not self._session.closed:
            await self._session.close()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _call(self, method: str, params: list) -> dict:
        call_id = str(uuid.uuid4())
        loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending[call_id] = fut
        await self._send({'msg': 'method', 'method': method, 'params': params, 'id': call_id})
        return await asyncio.wait_for(fut, timeout=15)

    async def _send(self, data: dict) -> None:
        if self._ws and not self._ws.closed:
            await self._ws.send_str(json.dumps(data))

    async def _listen(self) -> None:
        try:
            async for raw in self._ws:
                if raw.type == aiohttp.WSMsgType.TEXT:
                    try:
                        await self._dispatch(json.loads(raw.data))
                    except Exception as e:
                        log.debug('DDP dispatch error: %s', e)
                elif raw.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSE):
                    log.warning('DDP WebSocket closed (type=%s)', raw.type)
                    break
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.warning('DDP listener crashed: %s', e)
        finally:
            self.connected = False

    async def _dispatch(self, data: dict) -> None:
        msg_type = data.get('msg')

        if msg_type == 'ping':
            await self._send({'msg': 'pong'})

        elif msg_type == 'result':
            call_id = data.get('id')
            fut = self._pending.pop(call_id, None)
            if fut and not fut.done():
                if 'error' in data:
                    err = data['error']
                    fut.set_exception(DDPError(err.get('message', str(err))))
                else:
                    fut.set_result(data.get('result', {}))

        elif msg_type == 'changed':
            # Fan out to every registered callback
            for sub_id, cb in list(self._subscriptions.items()):
                try:
                    await cb(data)
                except Exception as e:
                    log.warning('DDP subscription callback %s error: %s', sub_id, e)
