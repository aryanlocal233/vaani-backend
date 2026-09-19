"""Text-to-speech abstraction: Bhashini primary, Azure Neural TTS optional fallback."""
from __future__ import annotations

from collections.abc import AsyncIterator

import structlog

from audio.audio_utils import chunk_bytes
from config import Settings
from services.bhashini_client import BhashiniAPIError, BhashiniClient

logger = structlog.get_logger(__name__)

TTS_STREAM_CHUNK_SIZE = 4096


class TTSService:
    def __init__(self, bhashini_client: BhashiniClient, settings: Settings) -> None:
        self._bhashini = bhashini_client
        self._settings = settings

    async def synthesize_stream(self, text: str, language: str) -> AsyncIterator[bytes]:
        if not text or not text.strip():
            return

        try:
            response = await self._bhashini.tts(text, language, sampling_rate=self._settings.AUDIO_SAMPLE_RATE)
            logger.info("tts_success", provider="bhashini", bytes=len(response.audio_pcm))
            for chunk in chunk_bytes(response.audio_pcm, TTS_STREAM_CHUNK_SIZE):
                yield chunk
        except BhashiniAPIError as exc:
            logger.warning("tts_bhashini_failed", error=str(exc))
            if self._settings.AZURE_SPEECH_KEY:
                async for chunk in self._synthesize_azure(text, language):
                    yield chunk
            else:
                raise

    async def _synthesize_azure(self, text: str, language: str) -> AsyncIterator[bytes]:
        """Azure Neural TTS fallback. Imported lazily; not a hard dependency."""
        try:
            import azure.cognitiveservices.speech as speechsdk  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "AZURE_SPEECH_KEY is set but azure-cognitiveservices-speech is not installed"
            ) from exc

        speech_config = speechsdk.SpeechConfig(
            subscription=self._settings.AZURE_SPEECH_KEY, region=self._settings.AZURE_SPEECH_REGION
        )
        speech_config.speech_synthesis_language = language
        speech_config.set_speech_synthesis_output_format(
            speechsdk.SpeechSynthesisOutputFormat.Raw16Khz16BitMonoPcm
        )

        synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=None)
        result = synthesizer.speak_text_async(text).get()

        logger.info("tts_success", provider="azure", bytes=len(result.audio_data))
        for chunk in chunk_bytes(result.audio_data, TTS_STREAM_CHUNK_SIZE):
            yield chunk
