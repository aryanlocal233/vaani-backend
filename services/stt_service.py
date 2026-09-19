"""Speech-to-text abstraction: Bhashini primary, Azure Speech optional fallback."""
from __future__ import annotations

import time
from dataclasses import dataclass

import structlog

from config import Settings
from services.bhashini_client import BhashiniAPIError, BhashiniClient

logger = structlog.get_logger(__name__)


@dataclass
class STTResult:
    text: str
    language: str
    confidence: float
    duration_ms: float
    provider: str = "bhashini"


class STTService:
    def __init__(self, bhashini_client: BhashiniClient, settings: Settings) -> None:
        self._bhashini = bhashini_client
        self._settings = settings

    async def transcribe(self, pcm_data: bytes, language: str) -> STTResult:
        start = time.perf_counter()
        try:
            response = await self._bhashini.asr(pcm_data, language, self._settings.AUDIO_SAMPLE_RATE)
            duration_ms = (time.perf_counter() - start) * 1000
            logger.info("stt_success", provider="bhashini", duration_ms=duration_ms, lang=language)
            return STTResult(
                text=response.text,
                language=language,
                confidence=1.0,
                duration_ms=duration_ms,
                provider="bhashini",
            )
        except BhashiniAPIError as exc:
            logger.warning("stt_bhashini_failed", error=str(exc))
            if self._settings.AZURE_SPEECH_KEY:
                return await self._transcribe_azure(pcm_data, language, start)
            raise

    async def _transcribe_azure(self, pcm_data: bytes, language: str, start: float) -> STTResult:
        """Azure Cognitive Services Speech fallback.

        Requires the `azure-cognitiveservices-speech` SDK, which is intentionally not
        a hard dependency of this service (Bhashini is primary). Import lazily so the
        app runs fine without it when Azure credentials are absent.
        """
        try:
            import azure.cognitiveservices.speech as speechsdk  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "AZURE_SPEECH_KEY is set but azure-cognitiveservices-speech is not installed"
            ) from exc

        speech_config = speechsdk.SpeechConfig(
            subscription=self._settings.AZURE_SPEECH_KEY, region=self._settings.AZURE_SPEECH_REGION
        )
        speech_config.speech_recognition_language = language

        stream = speechsdk.audio.PushAudioInputStream()
        audio_config = speechsdk.audio.AudioConfig(stream=stream)
        recognizer = speechsdk.SpeechRecognizer(speech_config=speech_config, audio_config=audio_config)

        stream.write(pcm_data)
        stream.close()

        result = recognizer.recognize_once()
        duration_ms = (time.perf_counter() - start) * 1000
        logger.info("stt_success", provider="azure", duration_ms=duration_ms, lang=language)
        return STTResult(
            text=result.text,
            language=language,
            confidence=0.9,
            duration_ms=duration_ms,
            provider="azure",
        )
