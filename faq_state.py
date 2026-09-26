"""In-memory FAQ_ENTRIES cache, shared between the WS pipeline (sarvam_server.py)
and the admin routes (admin.py) so an admin edit takes effect immediately --
without this shared module, each would import the other and create a cycle."""
from __future__ import annotations

import difflib
import logging
import re

import db
import pack_config

logger = logging.getLogger("vaani.faq_state")

FAQ_ENTRIES: list[dict] = []

_WORD_RE = re.compile(r"\w+", re.UNICODE)

# Token-overlap ratio (see match_faq) required for a fuzzy match to count. Deliberately
# conservative -- a false match here means an unrelated question gets answered wrong, which is
# worse than the (rare) case of a real paraphrase falling through to the paid pipeline instead.
FUZZY_THRESHOLD = 0.78

# How close two individual words need to be (via difflib's ratio) to count as "the same word" --
# catches STT near-misses/minor spelling variance without being loose enough to conflate two
# genuinely different short words.
WORD_SIMILARITY_THRESHOLD = 0.80


async def reload() -> None:
    global FAQ_ENTRIES
    FAQ_ENTRIES = await db.load_pack(pack_config.EVENT_PACK)
    logger.info("Loaded %d FAQ entries for pack '%s'", len(FAQ_ENTRIES), pack_config.EVENT_PACK)


def _tokenize(text: str) -> set[str]:
    return set(_WORD_RE.findall(text.lower()))


def match_faq(transcript: str, lang: str) -> tuple[dict, str] | None:
    """Returns (matched_entry, match_type) where match_type is "exact" or "fuzzy", or None.

    Two tiers, cheapest first:

    Tier 1 (exact substring): today's original matching -- a configured keyword appears verbatim
    in the transcript. Near-zero cost, catches the question asked in the same words it was
    configured with, or with extra words around it.

    Tier 2 (fuzzy token-overlap): catches paraphrased questions that don't contain any keyword
    verbatim -- reordered words, an extra/missing word, an STT near-miss on one word -- without
    the infra cost of an embeddings model + vector search, which isn't justified at this pack's
    size (a handful to a few dozen FAQ entries: a linear scan here costs microseconds). This is
    exactly what turns "similar but not identical" questions into cache hits instead of paid
    STT+NMT+TTS calls, which is where most of the cost savings at scale actually come from --
    pilgrims practically never ask a repeat question in *exactly* the same words.

    Only runs when tier 1 finds nothing, so it never slows down the already-fast exact-match path.
    """
    normalized = transcript.strip().lower()
    if not normalized:
        return None

    for entry in FAQ_ENTRIES:
        for keyword in entry["keywords"].get(lang, ()):
            if keyword.lower() in normalized:
                return entry, "exact"

    transcript_tokens = _tokenize(normalized)
    if not transcript_tokens:
        return None

    best_entry = None
    best_score = 0.0
    for entry in FAQ_ENTRIES:
        for keyword in entry["keywords"].get(lang, ()):
            keyword_tokens = _tokenize(keyword)
            if not keyword_tokens:
                continue
            matched = sum(
                1 for kt in keyword_tokens
                if kt in transcript_tokens
                or any(difflib.SequenceMatcher(None, kt, tt).ratio() >= WORD_SIMILARITY_THRESHOLD for tt in transcript_tokens)
            )
            score = matched / len(keyword_tokens)
            if score > best_score:
                best_score = score
                best_entry = entry

    if best_entry is not None and best_score >= FUZZY_THRESHOLD:
        return best_entry, "fuzzy"
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
