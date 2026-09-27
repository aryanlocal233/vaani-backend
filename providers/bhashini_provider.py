"""Bhashini (govt. National Language Translation Mission) adapter -- wraps the existing,
already-tested services/bhashini_client.py (see tests/test_bhashini_client.py) rather than
reimplementing the ULCA pipeline request shape.

Bhashini's ULCA API already uses Vaani's own short language codes directly (confirmed by its
existing test suite: `client.asr(pcm, "hi")`), so no code translation is needed here, unlike
Sarvam/Azure which need their own BCP-47-style codes internally.

Bhashini's ASR always requires a specific source language up front -- there is no auto-detect
in the ULCA pipeline shape used here, so this cannot serve Vaani's open-set visitor-language
detection on its own; it's only usable once a language is already known (a repeat utterance in
an already-detected language, or the fixed operator side).
"""
from __future__ import annotations

from services.bhashini_client import BhashiniAPIError, BhashiniClient
from providers.base import ProviderError, ProviderNotConfigured, STTProvider, STTResult, TranslateProvider, TranslateResult, TTSProvider, TTSResult

# The 22 languages the National Language Translation Mission covers by mandate -- includes every
# language Sarvam currently gates behind its own beta approval (Maithili, Urdu, Nepali, Sanskrit,
# Santali, Konkani, Kashmiri, Manipuri, Bodo, Dogri, Sindhi), plus Vaani's original 12.
SUPPORTED_LANGUAGES = frozenset({
    "as", "bn", "brx", "doi", "gu", "hi", "kn", "ks", "kok", "mai", "ml", "mni",
    "mr", "ne", "or", "pa", "sa", "sat", "sd", "ta", "te", "ur", "en",
})

_client: BhashiniClient | None = None


def _get_client() -> BhashiniClient:
    global _client
    if _client is None:
        from config import get_settings
        _client = BhashiniClient(get_settings())
    return _client


def _is_configured() -> bool:
    from config import get_settings
    settings = get_settings()
    return bool(settings.BHASHINI_API_KEY and settings.BHASHINI_USER_ID)


class BhashiniSTT(STTProvider):
    name = "bhashini"
    supports_auto_detect = False
    languages = SUPPORTED_LANGUAGES

    def is_configured(self) -> bool:
        return _is_configured()

    async def transcribe(self, pcm_data: bytes, language_hint: str | None) -> STTResult:
        if not self.is_configured():
            raise ProviderNotConfigured("Bhashini API key/user ID not set")
        if not language_hint or language_hint == "unknown":
            raise ProviderError("Bhashini ASR requires a specific language -- it cannot auto-detect")
        try:
            result = await _get_client().asr(pcm_data, language_hint)
        except BhashiniAPIError as exc:
            raise ProviderError(str(exc)) from exc
        return STTResult(text=result.text, detected_language=language_hint, provider=self.name)


class BhashiniTranslate(TranslateProvider):
    name = "bhashini"
    languages = SUPPORTED_LANGUAGES

    def is_configured(self) -> bool:
        return _is_configured()

    async def translate(self, text: str, src_lang: str, tgt_lang: str) -> TranslateResult:
        if not self.is_configured():
            raise ProviderNotConfigured("Bhashini API key/user ID not set")
        try:
            result = await _get_client().translate(text, src_lang, tgt_lang)
        except BhashiniAPIError as exc:
            raise ProviderError(str(exc)) from exc
        return TranslateResult(text=result.translated_text, provider=self.name)


class BhashiniTTS(TTSProvider):
    name = "bhashini"
    languages = SUPPORTED_LANGUAGES

    def is_configured(self) -> bool:
        return _is_configured()

    async def synthesize(self, text: str, lang: str) -> TTSResult:
        if not self.is_configured():
            raise ProviderNotConfigured("Bhashini API key/user ID not set")
        try:
            result = await _get_client().tts(text, lang)
        except BhashiniAPIError as exc:
            raise ProviderError(str(exc)) from exc
        return TTSResult(pcm_bytes=result.audio_pcm, provider=self.name)
