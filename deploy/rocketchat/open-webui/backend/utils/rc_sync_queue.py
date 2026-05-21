"""
Persistent Rocket.Chat sync queue with exponential backoff.

Every sync operation that used to be `asyncio.create_task(...)` (fire-and-forget)
now goes through this queue.  Jobs are persisted to a JSON file so a Rocket.Chat
outage or an Open WebUI restart never silently loses a sync.

Architecture
------------
- A single background worker (started in main.py lifespan) drains the queue.
- Each job has: id, type, payload, attempts, next_attempt_at, created_at.
- On handler failure, the job's next_attempt_at is pushed forward by an
  exponential backoff (2s → 4s → 8s … capped at 1 hour). After
  ``MAX_ATTEMPTS`` the job is moved to a dead-letter file for inspection.
- Snapshots required for delete jobs (channel name, RC room id) are captured
  by the producer at enqueue time so the job can still run after the row is
  removed from the OW database.

Usage
-----
    from open_webui.utils import rc_sync_queue
    await rc_sync_queue.enqueue('user.ensure', {'user_id': user.id})

The handlers themselves live in rocketchat_sync.py.  They are registered with
the queue at module import time so the queue does not have to know about the
implementation details of each job type.
"""

import asyncio
import json
import logging
import os
import secrets
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MAX_ATTEMPTS = 12
BASE_BACKOFF = 2.0         # seconds
MAX_BACKOFF = 60 * 60      # 1 hour cap
WORKER_TICK = 1.0          # how often the worker wakes up

_QUEUE_DIR = Path(os.environ.get('RC_SYNC_QUEUE_DIR') or '') if os.environ.get(
    'RC_SYNC_QUEUE_DIR'
) else None


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class Job:
    id: str
    type: str
    payload: Dict[str, Any]
    attempts: int = 0
    last_error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    next_attempt_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict) -> 'Job':
        return cls(
            id=raw['id'],
            type=raw['type'],
            payload=raw.get('payload') or {},
            attempts=raw.get('attempts', 0),
            last_error=raw.get('last_error'),
            created_at=raw.get('created_at', time.time()),
            next_attempt_at=raw.get('next_attempt_at', time.time()),
        )


# ---------------------------------------------------------------------------
# Queue
# ---------------------------------------------------------------------------


class RCSyncQueue:
    def __init__(self) -> None:
        self._jobs: List[Job] = []
        self._handlers: Dict[str, Callable[[Dict[str, Any]], Awaitable[None]]] = {}
        self._lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._worker: Optional[asyncio.Task] = None
        self._running = False
        self._dir: Optional[Path] = None
        self._queue_file: Optional[Path] = None
        self._dead_file: Optional[Path] = None
        self._initialised = False

    # ---------- registration -------------------------------------------------

    def register(self, job_type: str, handler: Callable[[Dict[str, Any]], Awaitable[None]]) -> None:
        """Register a coroutine handler for a job type."""
        self._handlers[job_type] = handler

    # ---------- lifecycle ----------------------------------------------------

    def init(self, queue_dir: Optional[str] = None) -> None:
        """Resolve the on-disk path. Safe to call multiple times."""
        if self._initialised:
            return
        path = queue_dir or os.environ.get('RC_SYNC_QUEUE_DIR') or ''
        if not path:
            # Fall back to backend/open_webui/data so persistence is still on.
            try:
                from open_webui.env import DATA_DIR  # type: ignore
                path = str(Path(DATA_DIR) / 'rc_sync_queue')
            except Exception:
                path = str(Path.cwd() / 'rc_sync_queue')
        self._dir = Path(path)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._queue_file = self._dir / 'queue.json'
        self._dead_file = self._dir / 'dead_letter.json'
        self._initialised = True
        log.info('Rocket.Chat sync queue persisting to %s', self._dir)

    async def start(self) -> None:
        if self._running:
            return
        self.init()
        await self._load()
        self._running = True
        self._worker = asyncio.create_task(self._worker_loop(), name='rc-sync-queue-worker')
        log.info('Rocket.Chat sync queue worker started (%d pending jobs)', len(self._jobs))

    async def stop(self) -> None:
        self._running = False
        self._wake.set()
        if self._worker:
            try:
                await asyncio.wait_for(self._worker, timeout=5.0)
            except asyncio.TimeoutError:
                self._worker.cancel()
            self._worker = None

    # ---------- producer API -------------------------------------------------

    async def enqueue(self, job_type: str, payload: Dict[str, Any]) -> str:
        """Queue a sync job. Returns the job id."""
        if job_type not in self._handlers:
            log.warning('rc_sync_queue: enqueue called for unregistered type %r', job_type)
        self.init()
        job = Job(id=secrets.token_hex(8), type=job_type, payload=payload)
        async with self._lock:
            self._jobs.append(job)
            await self._persist_locked()
        self._wake.set()
        log.debug('rc_sync_queue enqueued %s id=%s payload=%s', job_type, job.id, payload)
        return job.id

    async def stats(self) -> Dict[str, Any]:
        """Return a snapshot of queue depth & oldest job age (for admin UIs)."""
        async with self._lock:
            now = time.time()
            return {
                'pending': len(self._jobs),
                'oldest_age_seconds': max((now - j.created_at for j in self._jobs), default=0),
                'types': sorted({j.type for j in self._jobs}),
                'attempts_total': sum(j.attempts for j in self._jobs),
                'queue_dir': str(self._dir) if self._dir else None,
            }

    async def list_jobs(self, limit: int = 200) -> List[dict]:
        """Return the first N pending jobs (for the admin dashboard)."""
        async with self._lock:
            return [j.to_dict() for j in self._jobs[:limit]]

    async def list_dead(self, limit: int = 200) -> List[dict]:
        """Return the first N dead-letter jobs."""
        if not self._dead_file or not self._dead_file.exists():
            return []
        try:
            with self._dead_file.open('r', encoding='utf-8') as f:
                data = json.load(f) or []
            return data[-limit:]
        except Exception as e:
            log.warning('rc_sync_queue: cannot read dead letter file: %s', e)
            return []

    async def replay_dead(self) -> int:
        """Move every dead-letter job back to the live queue. Returns count."""
        if not self._dead_file or not self._dead_file.exists():
            return 0
        try:
            with self._dead_file.open('r', encoding='utf-8') as f:
                items = json.load(f) or []
        except Exception:
            return 0
        moved = 0
        async with self._lock:
            for raw in items:
                try:
                    job = Job.from_dict(raw)
                    job.attempts = 0
                    job.next_attempt_at = time.time()
                    job.last_error = None
                    self._jobs.append(job)
                    moved += 1
                except Exception:
                    continue
            await self._persist_locked()
        try:
            self._dead_file.unlink()
        except Exception:
            pass
        self._wake.set()
        return moved

    # ---------- internals: worker -------------------------------------------

    async def _worker_loop(self) -> None:
        while self._running:
            try:
                await self._tick()
            except Exception as e:
                log.exception('rc_sync_queue worker tick error: %s', e)
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=WORKER_TICK)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()

    async def _tick(self) -> None:
        now = time.time()
        async with self._lock:
            due = [j for j in self._jobs if j.next_attempt_at <= now]
        for job in due:
            handler = self._handlers.get(job.type)
            if handler is None:
                log.warning('rc_sync_queue: no handler for %s, dropping', job.type)
                async with self._lock:
                    self._remove_locked(job.id)
                    await self._persist_locked()
                continue
            try:
                await handler(dict(job.payload))
                async with self._lock:
                    self._remove_locked(job.id)
                    await self._persist_locked()
                log.debug('rc_sync_queue completed %s id=%s', job.type, job.id)
            except Exception as e:
                async with self._lock:
                    job.attempts += 1
                    job.last_error = repr(e)[:500]
                    if job.attempts >= MAX_ATTEMPTS:
                        log.error(
                            'rc_sync_queue: %s id=%s exhausted retries (%d): %s',
                            job.type, job.id, job.attempts, e,
                        )
                        await self._dead_letter_locked(job)
                        self._remove_locked(job.id)
                    else:
                        delay = min(MAX_BACKOFF, BASE_BACKOFF * (2 ** (job.attempts - 1)))
                        job.next_attempt_at = time.time() + delay
                        log.warning(
                            'rc_sync_queue: %s id=%s failed attempt %d (%s) — retry in %.0fs',
                            job.type, job.id, job.attempts, e, delay,
                        )
                    await self._persist_locked()

    def _remove_locked(self, job_id: str) -> None:
        self._jobs = [j for j in self._jobs if j.id != job_id]

    # ---------- internals: persistence --------------------------------------

    async def _persist_locked(self) -> None:
        if not self._queue_file:
            return
        data = [j.to_dict() for j in self._jobs]
        try:
            tmp = self._queue_file.with_suffix('.json.tmp')
            with tmp.open('w', encoding='utf-8') as f:
                json.dump(data, f)
            os.replace(tmp, self._queue_file)
        except Exception as e:
            log.warning('rc_sync_queue: persist failed: %s', e)

    async def _load(self) -> None:
        if not self._queue_file or not self._queue_file.exists():
            return
        try:
            with self._queue_file.open('r', encoding='utf-8') as f:
                raw = json.load(f) or []
            self._jobs = [Job.from_dict(r) for r in raw]
        except Exception as e:
            log.warning('rc_sync_queue: load failed (%s); starting empty', e)
            self._jobs = []

    async def _dead_letter_locked(self, job: Job) -> None:
        if not self._dead_file:
            return
        try:
            existing: list = []
            if self._dead_file.exists():
                with self._dead_file.open('r', encoding='utf-8') as f:
                    existing = json.load(f) or []
            existing.append(job.to_dict())
            tmp = self._dead_file.with_suffix('.json.tmp')
            with tmp.open('w', encoding='utf-8') as f:
                json.dump(existing, f)
            os.replace(tmp, self._dead_file)
        except Exception as e:
            log.warning('rc_sync_queue: dead-letter write failed: %s', e)


# ---------------------------------------------------------------------------
# Module-level singleton + thin convenience wrappers
# ---------------------------------------------------------------------------


_queue = RCSyncQueue()


def get_queue() -> RCSyncQueue:
    return _queue


def register(job_type: str, handler: Callable[[Dict[str, Any]], Awaitable[None]]) -> None:
    _queue.register(job_type, handler)


async def enqueue(job_type: str, payload: Dict[str, Any]) -> str:
    return await _queue.enqueue(job_type, payload)


async def start() -> None:
    await _queue.start()


async def stop() -> None:
    await _queue.stop()


async def stats() -> Dict[str, Any]:
    return await _queue.stats()
