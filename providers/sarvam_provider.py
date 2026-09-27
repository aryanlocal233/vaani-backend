"""Sarvam AI adapter -- wraps the existing, already-verified sarvam_stt/translate/tts functions
in sarvam_server.py behind the common provider interface.

Imports sarvam_server lazily (inside each method, not at module level): sarvam_server.py will
import *this* package to route calls through the registry, so importing it back here at load
time would be a circular import. By the time any of these methods actually runs, the app is
already fully started and sarvam_server is fully loaded, so a deferred import is safe and avoids
a larger, riskier refactor of already-working code.
"""
from __future__ import annotations

from providers.base import ProviderError, STTProvider, STTResult, TranslateProvider, TranslateResult, TTSProvider, TTSResult

SUPPORTED_LANGUAGES = frozenset({
    "hi", "ta", "te", "bn", "kn", "mr", "gu", "pa", "ml", "or", "as", "en",
})


class SarvamSTT(STTProvider):
    name = "sarvam"
    supports_auto_detect = True
    languages = SUPPORTED_LANGUAGES

    def is_configured(self) -> bool:
        import sarvam_server
        return bool(sarvam_server.SARVAM_API_KEY)

    async def transcribe(self, pcm_data: bytes, language_hint: str | None) -> STTResult:
        import sarvam_server
        # Sarvam's STT wants "unknown" (auto-detect) or a full BCP-47 code like "hi-IN" -- never
        # a bare short code. The real pipeline always passes None (full auto-detect), but a
        # specific hint is possible once Bhashini/Azure need one, so convert it correctly here
        # too rather than assuming it never happens.
        if language_hint and language_hint != "unknown":
            sarvam_language_code = sarvam_server.LANGUAGE_CODE_MAP.get(language_hint, f"{language_hint}-IN")
        else:
            sarvam_language_code = "unknown"
        try:
            transcript, detected_bcp47 = await sarvam_server.sarvam_stt(pcm_data, sarvam_language_code)
        except sarvam_server.SarvamAPIError as exc:
            raise ProviderError(str(exc)) from exc
        detected_short = sarvam_server.REVERSE_LANGUAGE_CODE_MAP.get(detected_bcp47 or "")
        return STTResult(text=transcript, detected_language=detected_short, provider=self.name)


class SarvamTranslate(TranslateProvider):
    name = "sarvam"
    languages = SUPPORTED_LANGUAGES

    def is_configured(self) -> bool:
        import sarvam_server
        return bool(sarvam_server.SARVAM_API_KEY)

    async def translate(self, text: str, src_lang: str, tgt_lang: str) -> TranslateResult:
        import sarvam_server
        try:
            translated = await sarvam_server.sarvam_translate(text, src_lang, tgt_lang)
        except sarvam_server.SarvamAPIError as exc:
            raise ProviderError(str(exc)) from exc
        return TranslateResult(text=translated, provider=self.name)


class SarvamTTS(TTSProvider):
    name = "sarvam"
    languages = SUPPORTED_LANGUAGES

    def is_configured(self) -> bool:
        import sarvam_server
        return bool(sarvam_server.SARVAM_API_KEY)

    async def synthesize(self, text: str, lang: str) -> TTSResult:
        import sarvam_server
        try:
            pcm = await sarvam_server.sarvam_tts(text, lang)
        except sarvam_server.SarvamAPIError as exc:
            raise ProviderError(str(exc)) from exc
        return TTSResult(pcm_bytes=pcm, provider=self.name)
