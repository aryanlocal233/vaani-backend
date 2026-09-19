"""Redis-backed session state, keyed by session_id, one per WebSocket connection."""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass

import redis.asyncio as redis
import structlog

logger = structlog.get_logger(__name__)

SESSION_KEY_PREFIX = "vaani:session:"
ACTIVE_SESSIONS_SET = "vaani:sessions:active"


@dataclass
class SessionState:
    session_id: str
    src_lang: str
    tgt_lang: str
    created_at: float
    last_activity: float
    utterance_count: int = 0
    total_audio_bytes: int = 0

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, raw: str) -> "SessionState":
        return cls(**json.loads(raw))


class SessionManager:
    """Manages per-connection session state in Redis with a TTL."""

    def __init__(self, redis_client: redis.Redis, ttl_seconds: int = 3600) -> None:
        self._redis = redis_client
        self._ttl = ttl_seconds

    @staticmethod
    def _key(session_id: str) -> str:
        return f"{SESSION_KEY_PREFIX}{session_id}"

    async def create_session(self, src_lang: str, tgt_lang: str) -> SessionState:
        now = time.time()
        session = SessionState(
            session_id=str(uuid.uuid4()),
            src_lang=src_lang,
            tgt_lang=tgt_lang,
            created_at=now,
            last_activity=now,
        )
        await self._redis.set(self._key(session.session_id), session.to_json(), ex=self._ttl)
        await self._redis.sadd(ACTIVE_SESSIONS_SET, session.session_id)
        logger.info("session_created", session_id=session.session_id, src=src_lang, tgt=tgt_lang)
        return session

    async def get_session(self, session_id: str) -> SessionState | None:
        raw = await self._redis.get(self._key(session_id))
        if raw is None:
            return None
        return SessionState.from_json(raw)

    async def update_session(self, session_id: str, **kwargs) -> SessionState | None:
        session = await self.get_session(session_id)
        if session is None:
            return None
        for key, value in kwargs.items():
            if hasattr(session, key):
                setattr(session, key, value)
        session.last_activity = time.time()
        await self._redis.set(self._key(session_id), session.to_json(), ex=self._ttl)
        return session

    async def delete_session(self, session_id: str) -> None:
        await self._redis.delete(self._key(session_id))
        await self._redis.srem(ACTIVE_SESSIONS_SET, session_id)
        logger.info("session_deleted", session_id=session_id)

    async def get_active_count(self) -> int:
        return await self._redis.scard(ACTIVE_SESSIONS_SET)
