"""In-memory FAQ_ENTRIES cache, shared between the WS pipeline (sarvam_server.py)
and the admin routes (admin.py) so an admin edit takes effect immediately --
without this shared module, each would import the other and create a cycle."""
from __future__ import annotations

import logging

import db
import pack_config

logger = logging.getLogger("vaani.faq_state")

FAQ_ENTRIES: list[dict] = []


async def reload() -> None:
    global FAQ_ENTRIES
    FAQ_ENTRIES = await db.load_pack(pack_config.EVENT_PACK)
    logger.info("Loaded %d FAQ entries for pack '%s'", len(FAQ_ENTRIES), pack_config.EVENT_PACK)


def match_faq(transcript: str, lang: str) -> dict | None:
    normalized = transcript.strip().lower()
    if not normalized:
        return None
    for entry in FAQ_ENTRIES:
        for keyword in entry["keywords"].get(lang, ()):
            if keyword.lower() in normalized:
                return entry
    return None


def resolve_faq_answer(entry: dict, lang: str) -> tuple[str, str]:
    """Returns (answer_text, answer_lang) -- falls back to English, then any available language,
    if this FAQ has no answer authored for the detected language yet."""
    if lang in entry["answer"]:
        return entry["answer"][lang], lang
    if "en" in entry["answer"]:
        return entry["answer"]["en"], "en"
    fallback_lang, fallback_text = next(iter(entry["answer"].items()))
    return fallback_text, fallback_lang
