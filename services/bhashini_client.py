"""Async client for the Bhashini ULCA inference pipeline API.

Reference: https://bhashini.gov.in — Dhruva ASR/NMT/TTS inference endpoints.
All three task types (asr, translation, tts) share the same
`/services/inference/pipeline` endpoint; only `pipelineTasks[0]` differs.
"""
from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass

import httpx
import structlog

from config import Settings

logger = structlog.get_logger(__name__)

INFERENCE_PATH = "/services/inference/pipeline"

MAX_RETRIES = 3
RETRYABLE_STATUS_CODES = {429, 503}


class BhashiniAPIError(Exception):
    """Raised when the Bhashini API returns an unrecoverable error."""


@dataclass
class BhashiniSttResponse:
    text: str
    raw: dict


@dataclass
class BhashiniNmtResponse:
    translated_text: str
    raw: dict


@dataclass
class BhashiniTtsResponse:
    audio_pcm: bytes
    raw: dict


class BhashiniClient:
    """Thin async wrapper around the Bhashini ULCA pipeline endpoints."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=settings.BHASHINI_BASE_URL,
            timeout=30.0,
            limits=httpx.Limits(max_connections=50),
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _headers(self) -> dict:
        return {
            "Authorization": self._settings.BHASHINI_API_KEY,
            "userID": self._settings.BHASHINI_USER_ID,
            "Content-Type": "application/json",
        }

    async def _post_with_retry(self, payload: dict) -> dict:
        last_exc: Exception | None = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = await self._client.post(
                    INFERENCE_PATH, json=payload, headers=self._headers()
                )
                if response.status_code in RETRYABLE_STATUS_CODES and attempt < MAX_RETRIES:
                    backoff = 0.5 * (2 ** (attempt - 1))
                    logger.warning(
                        "bhashini_retryable_status",
                        status=response.status_code,
                        attempt=attempt,
                        backoff_s=backoff,
                    )
                    await asyncio.sleep(backoff)
                    continue
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                if exc.response.status_code not in RETRYABLE_STATUS_CODES:
                    raise BhashiniAPIError(
                        f"Bhashini API error {exc.response.status_code}: {exc.response.text}"
                    ) from exc
            except httpx.HTTPError as exc:
                last_exc = exc
                if attempt < MAX_RETRIES:
                    backoff = 0.5 * (2 ** (attempt - 1))
                    logger.warning("bhashini_transport_error", error=str(exc), attempt=attempt)
                    await asyncio.sleep(backoff)
                    continue

        raise BhashiniAPIError(f"Bhashini API request failed after {MAX_RETRIES} attempts") from last_exc

    async def asr(self, pcm_data: bytes, source_language: str, sampling_rate: int = 16000) -> BhashiniSttResponse:
        audio_b64 = base64.b64encode(pcm_data).decode("ascii")
        payload = {
            "pipelineTasks": [
                {
                    "taskType": "asr",
                    "config": {
                        "language": {"sourceLanguage": source_language},
                        "serviceId": "",
                        "audioFormat": "pcm",
                        "samplingRate": sampling_rate,
                    },
                }
            ],
            "inputData": {"audio": [{"audioContent": audio_b64}]},
        }
        raw = await self._post_with_retry(payload)
        text = self._extract_stt_text(raw)
        return BhashiniSttResponse(text=text, raw=raw)

    async def translate(self, text: str, source_language: str, target_language: str) -> BhashiniNmtResponse:
        payload = {
            "pipelineTasks": [
                {
                    "taskType": "translation",
                    "config": {
                        "language": {
                            "sourceLanguage": source_language,
                            "targetLanguage": target_language,
                        },
                        "serviceId": "",
                    },
                }
            ],
            "inputData": {"input": [{"source": text}]},
        }
        raw = await self._post_with_retry(payload)
        translated = self._extract_nmt_text(raw)
        return BhashiniNmtResponse(translated_text=translated, raw=raw)

    async def tts(
        self, text: str, target_language: str, gender: str = "female", sampling_rate: int = 16000
    ) -> BhashiniTtsResponse:
        payload = {
            "pipelineTasks": [
                {
                    "taskType": "tts",
                    "config": {
                        "language": {"sourceLanguage": target_language},
                        "serviceId": "",
                        "gender": gender,
                        "samplingRate": sampling_rate,
                    },
                }
            ],
            "inputData": {"input": [{"source": text}]},
        }
        raw = await self._post_with_retry(payload)
        audio_pcm = self._extract_tts_audio(raw)
        return BhashiniTtsResponse(audio_pcm=audio_pcm, raw=raw)

    @staticmethod
    def _extract_stt_text(raw: dict) -> str:
        try:
            return raw["pipelineResponse"][0]["output"][0]["source"]
        except (KeyError, IndexError, TypeError) as exc:
            raise BhashiniAPIError(f"Unexpected ASR response shape: {raw}") from exc

    @staticmethod
    def _extract_nmt_text(raw: dict) -> str:
        try:
            return raw["pipelineResponse"][0]["output"][0]["target"]
        except (KeyError, IndexError, TypeError) as exc:
            raise BhashiniAPIError(f"Unexpected NMT response shape: {raw}") from exc

    @staticmethod
    def _extract_tts_audio(raw: dict) -> bytes:
        try:
            audio_b64 = raw["pipelineResponse"][0]["audio"][0]["audioContent"]
        except (KeyError, IndexError, TypeError) as exc:
            raise BhashiniAPIError(f"Unexpected TTS response shape: {raw}") from exc
        return base64.b64decode(audio_b64)
