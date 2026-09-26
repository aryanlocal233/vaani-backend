"""In-memory kill-switch, mirroring faq_state.py's pattern: a module-level flag every request
checks, backed by Postgres so it survives a container restart, but read from memory (not the DB)
on the hot path so toggling it from the admin panel takes effect on the *next* utterance of every
open connection immediately -- not just new connections, and not delayed by a DB round trip."""
from __future__ import annotations

import logging

import asyncpg

import db
import pack_config

logger = logging.getLogger("vaani.runtime_state")

PAUSED = False

_pool: asyncpg.Pool | None = None


async def init() -> None:
    global _pool, PAUSED
    _pool = db.get_pool()
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT paused FROM knowledge_packs WHERE id = $1", pack_config.EVENT_PACK
        )
    PAUSED = bool(row["paused"]) if row is not None else False
    logger.info("Runtime state loaded: paused=%s", PAUSED)


async def set_paused(value: bool) -> None:
    global PAUSED
    PAUSED = value
    async with _pool.acquire() as conn:
        await conn.execute(
            "UPDATE knowledge_packs SET paused = $1 WHERE id = $2", value, pack_config.EVENT_PACK
        )
    logger.warning("API %s via admin panel", "PAUSED" if value else "RESUMED")
