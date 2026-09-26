"""Postgres access for Vaani's knowledge-pack system. One deployment loads
exactly one active pack (EVENT_PACK env var) at startup -- "Vaani App + Event
Knowledge Pack = Event Deployment". Adding a new event/mela is new pack rows
via migrate_faq.py, not a code change here.
"""
from __future__ import annotations

import datetime
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


def get_pool() -> asyncpg.Pool:
    return _pool


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
    audio_duration_ms: int = 0,
    stt_cost_inr: float = 0.0,
    translate_cost_inr: float = 0.0,
    tts_cost_inr: float = 0.0,
    device_id: str | None = None,
) -> None:
    async with _pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO conversation_analytics "
            "(pack_id, counter_id, detected_language, faq_id, cache_hit, translation_api_used, "
            " tts_api_used, stt_ms, response_ms, escalated, audio_duration_ms, "
            " stt_cost_inr, translate_cost_inr, tts_cost_inr, device_id) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)",
            pack_id, counter_id, detected_language, faq_id, cache_hit,
            translation_api_used, tts_api_used, stt_ms, response_ms, escalated,
            audio_duration_ms, stt_cost_inr, translate_cost_inr, tts_cost_inr, device_id,
        )


async def upsert_device(
    device_id: str, pack_id: str, device_model: str | None, os_version: str | None,
    app_version: str | None, counter_id: str,
) -> None:
    async with _pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO devices (device_id, pack_id, device_model, os_version, app_version, last_counter_id) "
            "VALUES ($1, $2, $3, $4, $5, $6) "
            "ON CONFLICT (device_id) DO UPDATE SET "
            "device_model = EXCLUDED.device_model, os_version = EXCLUDED.os_version, "
            "app_version = EXCLUDED.app_version, last_counter_id = EXCLUDED.last_counter_id, "
            "last_seen = now(), connection_count = devices.connection_count + 1",
            device_id, pack_id, device_model, os_version, app_version, counter_id,
        )


async def list_devices(pack_id: str) -> list[asyncpg.Record]:
    async with _pool.acquire() as conn:
        return await conn.fetch(
            "SELECT device_id, device_model, os_version, app_version, last_counter_id, "
            "first_seen, last_seen, connection_count "
            "FROM devices WHERE pack_id = $1 ORDER BY last_seen DESC",
            pack_id,
        )


def now_ms() -> int:
    return int(time.perf_counter() * 1000)


async def get_analytics_summary(pack_id: str) -> dict:
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT COUNT(*) AS total, "
            "COUNT(*) FILTER (WHERE ts::date = now()::date) AS today, "
            "COUNT(*) FILTER (WHERE cache_hit IN ('faq', 'text_cache')) AS cache_hits, "
            "COUNT(*) FILTER (WHERE cache_hit = 'no_speech') AS noise_triggers, "
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
        "noise_triggers": row["noise_triggers"] or 0,
        "escalated": row["escalated"] or 0,
    }


async def get_cost_summary(pack_id: str) -> dict:
    """Real cost, summed from what was actually logged per request (pricing.py rates applied
    at logging time) -- not recomputed here. cost_avoided_today is the one estimated figure:
    today's cache hits (FAQ + the general repeated-phrase cache) multiplied by the average
    translate+TTS cost of today's genuine cache-miss requests, i.e. "what today's repeats would
    have cost without caching, based on what your actual uncached requests cost today" --
    grounded in real observed averages, not an assumed rate."""
    async with _pool.acquire() as conn:
        today_row = await conn.fetchrow(
            "SELECT "
            "COALESCE(SUM(stt_cost_inr),0) AS stt, "
            "COALESCE(SUM(translate_cost_inr),0) AS translate, "
            "COALESCE(SUM(tts_cost_inr),0) AS tts, "
            "COUNT(*) FILTER (WHERE cache_hit IN ('faq', 'text_cache')) AS cache_hits, "
            "COUNT(*) FILTER (WHERE cache_hit = 'no_speech') AS noise_triggers, "
            "COALESCE(SUM(stt_cost_inr) FILTER (WHERE cache_hit = 'no_speech'), 0) AS noise_cost "
            "FROM conversation_analytics WHERE pack_id=$1 AND ts::date = now()::date",
            pack_id,
        )
        total_row = await conn.fetchrow(
            "SELECT COALESCE(SUM(stt_cost_inr + translate_cost_inr + tts_cost_inr), 0) AS total "
            "FROM conversation_analytics WHERE pack_id=$1",
            pack_id,
        )
        avg_row = await conn.fetchrow(
            "SELECT AVG(translate_cost_inr + tts_cost_inr) AS avg_full_cost "
            "FROM conversation_analytics "
            "WHERE pack_id=$1 AND ts::date = now()::date AND cache_hit = 'none' AND translation_api_used",
            pack_id,
        )

    today_total = float(today_row["stt"] + today_row["translate"] + today_row["tts"])
    avg_full_cost = float(avg_row["avg_full_cost"] or 0)
    cache_hits_today = today_row["cache_hits"] or 0

    return {
        "today_stt": float(today_row["stt"]),
        "today_translate": float(today_row["translate"]),
        "today_tts": float(today_row["tts"]),
        "today_total": today_total,
        "all_time_total": float(total_row["total"]),
        "cache_hits_today": cache_hits_today,
        "cost_avoided_today": cache_hits_today * avg_full_cost,
        "noise_triggers_today": today_row["noise_triggers"] or 0,
        "noise_cost_today": float(today_row["noise_cost"]),
    }


async def get_hourly_usage(pack_id: str, day: datetime.date) -> list[dict]:
    """Returns one row per hour that had at least one conversation on the given day."""
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT date_trunc('hour', ts) AS hour, "
            "COUNT(*) AS conversations, "
            "COUNT(*) FILTER (WHERE cache_hit='faq') AS cache_hits, "
            "SUM(stt_cost_inr + translate_cost_inr + tts_cost_inr) AS cost_inr "
            "FROM conversation_analytics "
            "WHERE pack_id = $1 AND ts::date = $2::date "
            "GROUP BY 1 ORDER BY 1",
            pack_id, day,
        )
    return [
        {
            "hour": row["hour"],
            "conversations": row["conversations"],
            "cache_hits": row["cache_hits"],
            "cost_inr": float(row["cost_inr"] or 0),
        }
        for row in rows
    ]


# ---------------------------------------------------------------------------
# General (non-FAQ) translation + TTS response cache
# ---------------------------------------------------------------------------

async def get_translation_cache(pack_id: str, src_lang: str, tgt_lang: str, text_hash: str) -> str | None:
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT translated_text FROM translation_cache "
            "WHERE pack_id=$1 AND src_lang=$2 AND tgt_lang=$3 AND text_hash=$4",
            pack_id, src_lang, tgt_lang, text_hash,
        )
        if row is not None:
            await conn.execute(
                "UPDATE translation_cache SET hit_count = hit_count + 1, last_used_at = now() "
                "WHERE pack_id=$1 AND src_lang=$2 AND tgt_lang=$3 AND text_hash=$4",
                pack_id, src_lang, tgt_lang, text_hash,
            )
    return row["translated_text"] if row is not None else None


async def save_translation_cache(
    pack_id: str, src_lang: str, tgt_lang: str, text_hash: str, source_text: str, translated_text: str
) -> None:
    async with _pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO translation_cache (pack_id, src_lang, tgt_lang, text_hash, source_text, translated_text) "
            "VALUES ($1,$2,$3,$4,$5,$6) "
            "ON CONFLICT (pack_id, src_lang, tgt_lang, text_hash) DO NOTHING",
            pack_id, src_lang, tgt_lang, text_hash, source_text, translated_text,
        )


async def get_general_tts_audio(pack_id: str, lang: str, voice: str, text_hash: str) -> bytes | None:
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT audio_path FROM tts_audio_cache WHERE pack_id=$1 AND lang=$2 AND voice=$3 AND text_hash=$4",
            pack_id, lang, voice, text_hash,
        )
        if row is not None:
            await conn.execute(
                "UPDATE tts_audio_cache SET hit_count = hit_count + 1, last_used_at = now() "
                "WHERE pack_id=$1 AND lang=$2 AND voice=$3 AND text_hash=$4",
                pack_id, lang, voice, text_hash,
            )
    if row is None:
        return None
    return read_audio_file(row["audio_path"])


async def save_general_tts_audio(pack_id: str, lang: str, voice: str, text_hash: str, pcm_bytes: bytes) -> str:
    cache_dir = os.path.join(AUDIO_CACHE_DIR, pack_id, "general")
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, f"{lang}_{voice}_{text_hash}.pcm")
    with open(path, "wb") as f:
        f.write(pcm_bytes)
    async with _pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO tts_audio_cache (pack_id, lang, voice, text_hash, audio_path) "
            "VALUES ($1,$2,$3,$4,$5) "
            "ON CONFLICT (pack_id, lang, voice, text_hash) DO UPDATE SET audio_path = EXCLUDED.audio_path",
            pack_id, lang, voice, text_hash, path,
        )
    return path


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
