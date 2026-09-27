"""Integration test against a real Postgres -- not a unit test, so not run by the default `pytest
tests/` pass. Needs DATABASE_URL pointing at a real server (the app container's network is the
only place that can reach vaani-postgres today -- see db.py/pack_config.py), so run it with:

    docker cp tests/test_db_load_pack_integration.py vaani-backend:/app/tests/
    docker exec vaani-backend python -m pytest tests/test_db_load_pack_integration.py -v

This exists specifically to regression-test a real bug: db.load_pack's keyword/answer queries
weren't filtered to status='approved' faq_ids the way the main query is, so any draft FAQ with
keywords (e.g. the Gaya Ji intents seeded by seed_gaya_intents.py) made load_pack raise KeyError
on the very next app restart -- it was silently latent from the first seed run until a later,
unrelated restart took the whole service down. This test would have caught it before deploy.

Uses a throwaway pack_id ('pytest_scratch_pack') so it can never touch real pilgrim-facing data,
and cleans up after itself (cascades via the FK) even if an assertion fails.
"""
from __future__ import annotations

import pytest

import db

PACK_ID = "pytest_scratch_pack"


@pytest.fixture(autouse=True)
async def _pool_and_cleanup():
    await db.init_pool()
    pool = db.get_pool()
    async with pool.acquire() as conn:
        # faq_knowledge.pack_id has a FK to knowledge_packs -- needs a real (throwaway) row first.
        await conn.execute(
            "INSERT INTO knowledge_packs (id, name) VALUES ($1, 'pytest scratch pack') "
            "ON CONFLICT (id) DO NOTHING",
            PACK_ID,
        )
    try:
        yield
    finally:
        async with pool.acquire() as conn:
            # Cascades to faq_keywords/faq_answers via their own FKs to faq_knowledge.
            await conn.execute("DELETE FROM faq_knowledge WHERE pack_id = $1", PACK_ID)
            await conn.execute("DELETE FROM knowledge_packs WHERE id = $1", PACK_ID)
        await db.close_pool()


@pytest.mark.asyncio
async def test_draft_faq_with_keywords_does_not_break_load_pack():
    await db.upsert_faq(PACK_ID, "draft_intent", "static", "draft", "pytest")
    await db.replace_faq_keywords(PACK_ID, "draft_intent", "en", ["some draft keyword"])
    await db.upsert_faq_answer(PACK_ID, "draft_intent", "en", "[DRAFT]", "pytest", reviewed=False)

    # Must not raise -- this is the exact shape that used to KeyError.
    entries = await db.load_pack(PACK_ID)
    assert entries == []


@pytest.mark.asyncio
async def test_approved_faq_loads_correctly_alongside_a_draft():
    await db.upsert_faq(PACK_ID, "draft_intent", "static", "draft", "pytest")
    await db.replace_faq_keywords(PACK_ID, "draft_intent", "en", ["some draft keyword"])

    await db.upsert_faq(PACK_ID, "live_intent", "static", "approved", "pytest")
    await db.replace_faq_keywords(PACK_ID, "live_intent", "en", ["some live keyword"])
    await db.upsert_faq_answer(PACK_ID, "live_intent", "en", "A real approved answer", "pytest", reviewed=True)

    entries = await db.load_pack(PACK_ID)

    assert len(entries) == 1
    assert entries[0]["id"] == "live_intent"
    assert entries[0]["keywords"]["en"] == ["some live keyword"]
    assert entries[0]["answer"]["en"] == "A real approved answer"


@pytest.mark.asyncio
async def test_retired_faq_excluded_even_with_keywords_and_answers():
    await db.upsert_faq(PACK_ID, "old_intent", "static", "approved", "pytest")
    await db.replace_faq_keywords(PACK_ID, "old_intent", "en", ["old keyword"])
    await db.upsert_faq_answer(PACK_ID, "old_intent", "en", "Old answer", "pytest", reviewed=True)
    await db.upsert_faq(PACK_ID, "old_intent", "static", "retired", "pytest")

    entries = await db.load_pack(PACK_ID)
    assert entries == []
