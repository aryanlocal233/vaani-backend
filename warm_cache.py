"""Batch job: pre-synthesizes TTS audio for every approved FAQ answer across all supported
languages, so the *first* live request in any language already hits the audio cache instead of
paying for + waiting on a live TTS call mid-conversation. Safe to re-run -- already-cached
(pack, faq, language, voice, version) combinations are skipped.

Run after any FAQ answer edit in the admin panel (editing an answer already invalidates its own
stale cache entry -- this just re-warms it proactively instead of waiting for a live pilgrim to
be the one who pays the first-synthesis latency).
"""
import asyncio

from dotenv import load_dotenv

import db
import faq_state
import pack_config
from sarvam_server import faq_voice_for, get_faq_audio

load_dotenv()


async def main() -> None:
    await db.init_pool()
    await faq_state.reload()

    seen: set[tuple[str, str]] = set()
    synthesized = 0
    skipped = 0

    for entry in faq_state.FAQ_ENTRIES:
        for lang in pack_config.SUPPORTED_LANGUAGES:
            answer_text, answer_lang = faq_state.resolve_faq_answer(entry, lang)
            key = (entry["id"], answer_lang)
            if key in seen:
                continue
            seen.add(key)

            cached_path = await db.get_cached_audio_path(
                pack_config.EVENT_PACK, entry["id"], answer_lang, faq_voice_for(answer_lang)
            )
            if cached_path is not None:
                skipped += 1
                continue

            print(f"Synthesizing '{entry['id']}' in {answer_lang}...")
            await get_faq_audio(entry, answer_lang, answer_text)
            synthesized += 1

    print(f"\nDone. {synthesized} synthesized, {skipped} already cached.")
    await db.close_pool()


if __name__ == "__main__":
    asyncio.run(main())
