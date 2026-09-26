"""Postgres access for Vaani's knowledge-pack system. One deployment loads
exactly one active pack (EVENT_PACK env var) at startup -- "Vaani App + Event
Knowledge Pack = Event Deployment". Adding a new event/mela is new pack rows
via migrate_faq.py, not a code change here.
"""
from __future__ import annotations

import os
import time

import asyncpg

AUDIO_CACHE_DIR = "/app/audio_cache"

_pool: asyncpg.Pool | None = None


async def init_pool() -> None:
    global _pool
    _pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=10)


async def close_pool() -> None:
    if _pool is not None:
        await _pool.close()


async def load_pack(pack_id: str) -> list[dict]:
    """Loads one pack's FAQ entries into the same in-memory shape the pipeline
    already matches against: [{id, category, keywords: {lang: [...]}, answer: {lang: text}}]."""
    async with _pool.acquire() as conn:
        faqs = await conn.fetch(
            "SELECT faq_id, category FROM faq_knowledge WHERE pack_id = $1 AND status = 'approved'",
            pack_id,
        )
        keyword_rows = await conn.fetch(
            "SELECT faq_id, language, keyword FROM faq_keywords WHERE pack_id = $1", pack_id
        )
        answer_rows = await conn.fetch(
            "SELECT faq_id, language, answer_text FROM faq_answers WHERE pack_id = $1", pack_id
        )

    entries: dict[str, dict] = {
        row["faq_id"]: {"id": row["faq_id"], "category": row["category"], "keywords": {}, "answer": {}}
        for row in faqs
    }
    for row in keyword_rows:
        entries[row["faq_id"]]["keywords"].setdefault(row["language"], []).append(row["keyword"])
    for row in answer_rows:
        entries[row["faq_id"]]["answer"][row["language"]] = row["answer_text"]

    return list(entries.values())


async def get_cached_audio_path(pack_id: str, faq_id: str, lang: str, voice: str, version: int = 1) -> str | None:
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT audio_path FROM audio_cache WHERE pack_id=$1 AND faq_id=$2 AND language=$3 AND voice=$4 AND version=$5",
            pack_id, faq_id, lang, voice, version,
        )
    return row["audio_path"] if row else None


async def save_audio_cache(pack_id: str, faq_id: str, lang: str, voice: str, pcm_bytes: bytes, version: int = 1) -> str:
    pack_dir = os.path.join(AUDIO_CACHE_DIR, pack_id)
    os.makedirs(pack_dir, exist_ok=True)
    path = os.path.join(pack_dir, f"{faq_id}_{lang}_{voice}_v{version}.pcm")
    with open(path, "wb") as f:
        f.write(pcm_bytes)
    async with _pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO audio_cache (pack_id, faq_id, language, voice, version, audio_path) "
            "VALUES ($1, $2, $3, $4, $5, $6) "
            "ON CONFLICT (pack_id, faq_id, language, voice, version) DO UPDATE SET audio_path = EXCLUDED.audio_path",
            pack_id, faq_id, lang, voice, version, path,
        )
    return path


def read_audio_file(path: str) -> bytes | None:
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return f.read()


async def log_analytics(
    pack_id: str,
    counter_id: str,
    detected_language: str | None,
    faq_id: str | None,
    cache_hit: str,
    translation_api_used: bool,
    tts_api_used: bool,
    stt_ms: int | None,
    response_ms: int | None,
    escalated: bool = False,
) -> None:
    async with _pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO conversation_analytics "
            "(pack_id, counter_id, detected_language, faq_id, cache_hit, translation_api_used, "
            " tts_api_used, stt_ms, response_ms, escalated) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)",
            pack_id, counter_id, detected_language, faq_id, cache_hit,
            translation_api_used, tts_api_used, stt_ms, response_ms, escalated,
        )


def now_ms() -> int:
    return int(time.perf_counter() * 1000)


async def get_analytics_summary(pack_id: str) -> dict:
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT COUNT(*) AS total, "
            "COUNT(*) FILTER (WHERE ts::date = now()::date) AS today, "
            "COUNT(*) FILTER (WHERE cache_hit = 'faq') AS cache_hits, "
            "COUNT(*) FILTER (WHERE escalated) AS escalated "
            "FROM conversation_analytics WHERE pack_id = $1",
            pack_id,
        )
    total = row["total"] or 0
    cache_hits = row["cache_hits"] or 0
    return {
        "total": total,
        "today": row["today"] or 0,
        "cache_hits": cache_hits,
        "cache_hit_rate": round(cache_hits / total * 100, 1) if total else 0.0,
        "escalated": row["escalated"] or 0,
    }


# ---------------------------------------------------------------------------
# Admin auth
# ---------------------------------------------------------------------------

async def get_admin_by_username(username: str) -> asyncpg.Record | None:
    async with _pool.acquire() as conn:
        return await conn.fetchrow("SELECT id, username, password_hash FROM admin_users WHERE username = $1", username)


async def create_admin_user(username: str, password_hash: str) -> None:
    async with _pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO admin_users (username, password_hash) VALUES ($1, $2) "
            "ON CONFLICT (username) DO UPDATE SET password_hash = EXCLUDED.password_hash",
            username, password_hash,
        )


# ---------------------------------------------------------------------------
# Admin FAQ management
# ---------------------------------------------------------------------------

async def list_packs() -> list[asyncpg.Record]:
    async with _pool.acquire() as conn:
        return await conn.fetch("SELECT id, name, active FROM knowledge_packs ORDER BY id")


async def list_faqs(pack_id: str) -> list[dict]:
    async with _pool.acquire() as conn:
        faqs = await conn.fetch(
            "SELECT faq_id, category, status, version, updated_at FROM faq_knowledge "
            "WHERE pack_id = $1 ORDER BY faq_id",
            pack_id,
        )
        answer_rows = await conn.fetch(
            "SELECT faq_id, language FROM faq_answers WHERE pack_id = $1", pack_id
        )
    langs_by_faq: dict[str, list[str]] = {}
    for row in answer_rows:
        langs_by_faq.setdefault(row["faq_id"], []).append(row["language"])
    return [
        {**dict(row), "languages": sorted(langs_by_faq.get(row["faq_id"], []))}
        for row in faqs
    ]


async def get_faq_detail(pack_id: str, faq_id: str) -> dict | None:
    async with _pool.acquire() as conn:
        faq = await conn.fetchrow(
            "SELECT faq_id, category, status, version FROM faq_knowledge WHERE pack_id=$1 AND faq_id=$2",
            pack_id, faq_id,
        )
        if faq is None:
            return None
        keyword_rows = await conn.fetch(
            "SELECT language, keyword FROM faq_keywords WHERE pack_id=$1 AND faq_id=$2 ORDER BY language, keyword",
            pack_id, faq_id,
        )
        answer_rows = await conn.fetch(
            "SELECT language, answer_text, reviewed FROM faq_answers WHERE pack_id=$1 AND faq_id=$2 ORDER BY language",
            pack_id, faq_id,
        )
    keywords: dict[str, list[str]] = {}
    for row in keyword_rows:
        keywords.setdefault(row["language"], []).append(row["keyword"])
    answers = {row["language"]: {"text": row["answer_text"], "reviewed": row["reviewed"]} for row in answer_rows}
    return {**dict(faq), "keywords": keywords, "answers": answers}


async def upsert_faq(pack_id: str, faq_id: str, category: str, status: str, approved_by: str) -> None:
    async with _pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO faq_knowledge (pack_id, faq_id, category, status, approved_by, updated_at) "
            "VALUES ($1, $2, $3, $4, $5, now()) "
            "ON CONFLICT (pack_id, faq_id) DO UPDATE SET "
            "category = EXCLUDED.category, status = EXCLUDED.status, "
            "approved_by = EXCLUDED.approved_by, updated_at = now()",
            pack_id, faq_id, category, status, approved_by,
        )


async def replace_faq_keywords(pack_id: str, faq_id: str, language: str, keywords: list[str]) -> None:
    async with _pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "DELETE FROM faq_keywords WHERE pack_id=$1 AND faq_id=$2 AND language=$3",
                pack_id, faq_id, language,
            )
            for kw in keywords:
                kw = kw.strip()
                if kw:
                    await conn.execute(
                        "INSERT INTO faq_keywords (pack_id, faq_id, language, keyword) VALUES ($1,$2,$3,$4)",
                        pack_id, faq_id, language, kw,
                    )


async def upsert_faq_answer(pack_id: str, faq_id: str, language: str, answer_text: str, reviewed_by: str) -> None:
    """Writes the answer and invalidates any cached audio for this (pack, faq, language) --
    a changed approved_answer must never keep serving the old cached voice."""
    async with _pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO faq_answers (pack_id, faq_id, language, answer_text, reviewed, updated_at) "
            "VALUES ($1,$2,$3,$4,TRUE,now()) "
            "ON CONFLICT (pack_id, faq_id, language) DO UPDATE SET "
            "answer_text = EXCLUDED.answer_text, reviewed = TRUE, updated_at = now()",
            pack_id, faq_id, language, answer_text,
        )
    await invalidate_audio_cache(pack_id, faq_id, language)


async def invalidate_audio_cache(pack_id: str, faq_id: str, language: str | None = None) -> int:
    async with _pool.acquire() as conn:
        if language is None:
            rows = await conn.fetch(
                "SELECT audio_path FROM audio_cache WHERE pack_id=$1 AND faq_id=$2", pack_id, faq_id
            )
            await conn.execute("DELETE FROM audio_cache WHERE pack_id=$1 AND faq_id=$2", pack_id, faq_id)
        else:
            rows = await conn.fetch(
                "SELECT audio_path FROM audio_cache WHERE pack_id=$1 AND faq_id=$2 AND language=$3",
                pack_id, faq_id, language,
            )
            await conn.execute(
                "DELETE FROM audio_cache WHERE pack_id=$1 AND faq_id=$2 AND language=$3",
                pack_id, faq_id, language,
            )
    for row in rows:
        try:
            os.remove(row["audio_path"])
        except OSError:
            pass
    return len(rows)


async def create_faq(pack_id: str, faq_id: str, category: str, approved_by: str) -> None:
    async with _pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO faq_knowledge (pack_id, faq_id, category, status, approved_by) "
            "VALUES ($1, $2, $3, 'approved', $4)",
            pack_id, faq_id, category, approved_by,
        )
