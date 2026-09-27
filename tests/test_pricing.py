"""Unit tests for pricing.py -- the sole source of truth for real per-request cost, which every
cost-dashboard number and provider ranking decision (providers/registry.py's COST_TABLE) is
supposed to match. A silent drift here would misreport spend without any error being raised."""
import pricing


def test_stt_cost_one_minute():
    assert pricing.stt_cost(60_000) == pricing.STT_RATE_PER_MINUTE_INR


def test_stt_cost_zero_duration_is_free():
    assert pricing.stt_cost(0) == 0


def test_stt_cost_scales_linearly():
    assert pricing.stt_cost(120_000) == 2 * pricing.stt_cost(60_000)


def test_translate_cost_per_character():
    assert pricing.translate_cost(100) == 100 * pricing.TRANSLATE_RATE_PER_CHAR_INR


def test_translate_cost_zero_chars_is_free():
    assert pricing.translate_cost(0) == 0


def test_tts_cost_per_character():
    assert pricing.tts_cost(200) == 200 * pricing.TTS_RATE_PER_CHAR_INR
