"""Sarvam AI pay-as-you-go rates, confirmed directly against their pricing page
(https://www.sarvam.ai/api-pricing) on 2026-09-26 -- update these if Sarvam's
pricing changes; nothing else in the codebase should hardcode a rate."""

STT_RATE_PER_MINUTE_INR = 0.50       # ₹30/hour
TRANSLATE_RATE_PER_CHAR_INR = 0.005  # ₹0.005/character (pay-as-you-go tier)
TTS_RATE_PER_CHAR_INR = 0.003        # ₹3.00 per 1,000 characters


def stt_cost(audio_duration_ms: int) -> float:
    return (audio_duration_ms / 60_000) * STT_RATE_PER_MINUTE_INR


def translate_cost(char_count: int) -> float:
    return char_count * TRANSLATE_RATE_PER_CHAR_INR


def tts_cost(char_count: int) -> float:
    return char_count * TTS_RATE_PER_CHAR_INR
