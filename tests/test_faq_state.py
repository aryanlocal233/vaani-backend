"""Unit tests for faq_state.match_faq -- the confidence+margin-gated FAQ matcher that decides
whether a pilgrim's question gets a cached answer or falls through to the paid pipeline. Pure
logic, no DB/network -- FAQ_ENTRIES is monkeypatched directly rather than loaded via db.reload()."""
from __future__ import annotations

import pytest

import faq_state


@pytest.fixture(autouse=True)
def sample_entries(monkeypatch):
    entries = [
        {
            "id": "toilet", "category": "static",
            "keywords": {"hi": ["शौचालय कहाँ है"], "en": ["where is the toilet", "restroom location"]},
            "answer": {"hi": "शौचालय यहाँ है", "en": "The toilet is here"},
        },
        {
            "id": "pinddaan_cost", "category": "static",
            "keywords": {"en": ["cost of pind daan", "how much does pind daan cost"]},
            "answer": {"en": "[DRAFT]"},
        },
        {
            "id": "pandit_location", "category": "location",
            "keywords": {"en": ["where to find pandit", "find our family panda"]},
            "answer": {"en": "[DRAFT]"},
        },
    ]
    monkeypatch.setattr(faq_state, "FAQ_ENTRIES", entries)
    return entries


def test_exact_substring_match_wins_with_full_confidence():
    result = faq_state.match_faq("hi where is the toilet please", "en")
    assert result is not None
    entry, match_type, confidence = result
    assert entry["id"] == "toilet"
    assert match_type == "exact"
    assert confidence == 1.0


def test_no_match_returns_none():
    assert faq_state.match_faq("what time does the shop open", "en") is None


def test_empty_transcript_returns_none():
    assert faq_state.match_faq("   ", "en") is None


def test_fuzzy_match_on_reordered_words():
    # Same words as the "how much does pind daan cost" keyword, reordered and missing "does" --
    # not a substring match (must not hit the exact tier), but enough token overlap for fuzzy.
    result = faq_state.match_faq("pind daan cost how much", "en")
    assert result is not None
    entry, match_type, _ = result
    assert entry["id"] == "pinddaan_cost"
    assert match_type == "fuzzy"


def test_fuzzy_match_below_threshold_falls_through():
    # Only "pind" and "daan" overlap out of a 5-token keyword -- well under FUZZY_THRESHOLD.
    assert faq_state.match_faq("pind daan", "en") is None


def test_ambiguous_close_scores_fall_through_instead_of_guessing(monkeypatch):
    # Two entries scoring within FUZZY_MARGIN of each other: matching neither confidently is
    # the whole point of the margin gate -- verifies it, not just the threshold alone.
    monkeypatch.setattr(faq_state, "FAQ_ENTRIES", [
        {"id": "a", "category": "static", "keywords": {"en": ["where is the water point today"]}, "answer": {}},
        {"id": "b", "category": "static", "keywords": {"en": ["where is the food point today"]}, "answer": {}},
    ])
    assert faq_state.match_faq("where is the point today", "en") is None


def test_language_isolation_no_cross_language_match():
    # An English keyword must not match when matching against Hindi transcripts of the same entry.
    assert faq_state.match_faq("where is the toilet", "hi") is None


def test_resolve_faq_answer_prefers_requested_language():
    entry = {"answer": {"hi": "हिंदी उत्तर", "en": "English answer"}}
    text, lang = faq_state.resolve_faq_answer(entry, "hi")
    assert (text, lang) == ("हिंदी उत्तर", "hi")


def test_resolve_faq_answer_falls_back_to_english():
    entry = {"answer": {"en": "English answer", "ta": "Tamil answer"}}
    text, lang = faq_state.resolve_faq_answer(entry, "bn")
    assert (text, lang) == ("English answer", "en")


def test_resolve_faq_answer_falls_back_to_any_available_language():
    entry = {"answer": {"ta": "Tamil answer"}}
    text, lang = faq_state.resolve_faq_answer(entry, "bn")
    assert (text, lang) == ("Tamil answer", "ta")
