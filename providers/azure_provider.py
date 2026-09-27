"""Azure AI Speech + Azure Translator adapter -- plain REST calls (not the
azure-cognitiveservices-speech SDK), so this adds no native/binary dependency to the Docker
image. Needs AZURE_SPEECH_KEY + AZURE_SPEECH_REGION (Speech: STT+TTS) and
AZURE_TRANSLATOR_KEY + AZURE_TRANSLATOR_REGION (Translator is a separate Azure resource).

Voice names follow Azure's documented `{locale}-{Name}Neural` convention -- picked from Azure's
published voice list, but not live-verified against a real key (none was available while writing
this). Confirm the voice list once real credentials are added; a wrong voice name fails loudly
(400 from Azure) rather than silently, so this degrades safely either way.
"""
from __future__ import annotations

import os
import struct

import httpx

from providers.base import ProviderError, ProviderNotConfigured, STTProvider, STTResult, TranslateProvider, TranslateResult, TTSProvider, TTSResult

SUPPORTED_LANGUAGES = frozenset({
    "hi", "ta", "te", "bn", "kn", "mr", "gu", "pa", "ml", "or", "as", "en",
})

LOCALE_MAP = {
    "hi": "hi-IN", "ta": "ta-IN", "te": "te-IN", "bn": "bn-IN", "kn": "kn-IN",
    "mr": "mr-IN", "gu": "gu-IN", "pa": "pa-IN", "ml": "ml-IN", "or": "or-IN",
    "as": "as-IN", "en": "en-IN",
}

# Azure Translator's own language codes match Vaani's short codes directly for all 12, except
# Odia is "or" in both -- no separate map needed here, unlike Sarvam's od-IN quirk.

VOICE_MAP = {
    "hi-IN": "hi-IN-SwaraNeural", "ta-IN": "ta-IN-PallaviNeural", "te-IN": "te-IN-ShrutiNeural",
    "bn-IN": "bn-IN-TanishaaNeural", "kn-IN": "kn-IN-SapnaNeural", "mr-IN": "mr-IN-AarohiNeural",
    "gu-IN": "gu-IN-DhwaniNeural", "pa-IN": "pa-IN-VaaniNeural", "ml-IN": "ml-IN-SobhanaNeural",
    "or-IN": "or-IN-SubhasiniNeural", "as-IN": "as-IN-YashicaNeural", "en-IN": "en-IN-NeerjaNeural",
}

_http_client: httpx.AsyncClient | None = None


def _get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(timeout=20.0)
    return _http_client


def _speech_configured() -> bool:
    return bool(os.environ.get("AZURE_SPEECH_KEY") and os.environ.get("AZURE_SPEECH_REGION"))


def _translator_configured() -> bool:
    return bool(os.environ.get("AZURE_TRANSLATOR_KEY") and os.environ.get("AZURE_TRANSLATOR_REGION"))


def _pcm_to_wav(pcm_bytes: bytes, sample_rate: int = 16000) -> bytes:
    data_size = len(pcm_bytes)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI", b"RIFF", 36 + data_size, b"WAVE", b"fmt ", 16, 1, 1,
        sample_rate, sample_rate * 2, 2, 16, b"data", data_size,
    )
    return header + pcm_bytes


class AzureSTT(STTProvider):
    name = "azure"
    supports_auto_detect = False  # REST recognition endpoint needs a language per request
    languages = SUPPORTED_LANGUAGES

    def is_configured(self) -> bool:
        return _speech_configured()

    async def transcribe(self, pcm_data: bytes, language_hint: str | None) -> STTResult:
        if not self.is_configured():
            raise ProviderNotConfigured("AZURE_SPEECH_KEY/AZURE_SPEECH_REGION not set")
        if not language_hint or language_hint == "unknown":
            raise ProviderError("Azure STT (REST recognition endpoint) requires a specific language")

        locale = LOCALE_MAP.get(language_hint, f"{language_hint}-IN")
        region = os.environ["AZURE_SPEECH_REGION"]
        url = f"https://{region}.stt.speech.microsoft.com/speech/recognition/conversation/cognitiveservices/v1"

        try:
            response = await _get_http_client().post(
                url,
                params={"language": locale, "format": "simple"},
                headers={
                    "Ocp-Apim-Subscription-Key": os.environ["AZURE_SPEECH_KEY"],
                    "Content-Type": "audio/wav; codecs=audio/pcm; samplerate=16000",
                },
                content=_pcm_to_wav(pcm_data),
            )
            response.raise_for_status()
            body = response.json()
        except httpx.HTTPError as exc:
            raise ProviderError(f"Azure STT request failed: {exc}") from exc

        if body.get("RecognitionStatus") != "Success":
            raise ProviderError(f"Azure STT: {body.get('RecognitionStatus', 'unknown status')}")

        return STTResult(text=body.get("DisplayText", ""), detected_language=language_hint, provider=self.name)


class AzureTranslate(TranslateProvider):
    name = "azure"
    languages = SUPPORTED_LANGUAGES

    def is_configured(self) -> bool:
        return _translator_configured()

    async def translate(self, text: str, src_lang: str, tgt_lang: str) -> TranslateResult:
        if not self.is_configured():
            raise ProviderNotConfigured("AZURE_TRANSLATOR_KEY/AZURE_TRANSLATOR_REGION not set")

        url = "https://api.cognitive.microsofttranslator.com/translate"
        try:
            response = await _get_http_client().post(
                url,
                params={"api-version": "3.0", "from": src_lang, "to": tgt_lang},
                headers={
                    "Ocp-Apim-Subscription-Key": os.environ["AZURE_TRANSLATOR_KEY"],
                    "Ocp-Apim-Subscription-Region": os.environ["AZURE_TRANSLATOR_REGION"],
                    "Content-Type": "application/json",
                },
                json=[{"Text": text}],
            )
            response.raise_for_status()
            body = response.json()
        except httpx.HTTPError as exc:
            raise ProviderError(f"Azure Translate request failed: {exc}") from exc

        try:
            translated = body[0]["translations"][0]["text"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f"Unexpected Azure Translate response shape: {body}") from exc

        return TranslateResult(text=translated, provider=self.name)


class AzureTTS(TTSProvider):
    name = "azure"
    languages = SUPPORTED_LANGUAGES

    def is_configured(self) -> bool:
        return _speech_configured()

    async def synthesize(self, text: str, lang: str) -> TTSResult:
        if not self.is_configured():
            raise ProviderNotConfigured("AZURE_SPEECH_KEY/AZURE_SPEECH_REGION not set")

        locale = LOCALE_MAP.get(lang, f"{lang}-IN")
        voice = VOICE_MAP.get(locale, "en-IN-NeerjaNeural")
        region = os.environ["AZURE_SPEECH_REGION"]
        url = f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1"

        escaped_text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        ssml = (
            f'<speak version="1.0" xml:lang="{locale}">'
            f'<voice name="{voice}">{escaped_text}</voice></speak>'
        )

        try:
            response = await _get_http_client().post(
                url,
                headers={
                    "Ocp-Apim-Subscription-Key": os.environ["AZURE_SPEECH_KEY"],
                    "Content-Type": "application/ssml+xml",
                    "X-Microsoft-OutputFormat": "raw-16khz-16bit-mono-pcm",
                },
                content=ssml.encode("utf-8"),
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderError(f"Azure TTS request failed: {exc}") from exc

        return TTSResult(pcm_bytes=response.content, provider=self.name)
