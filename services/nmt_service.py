"""Neural machine translation abstraction: Bhashini primary, Google Translate optional fallback."""
from __future__ import annotations

import time
from dataclasses import dataclass

import httpx
import structlog

from config import Settings
from services.bhashini_client import BhashiniAPIError, BhashiniClient

logger = structlog.get_logger(__name__)


@dataclass
class NMTResult:
    translated_text: str
    src_lang: str
    tgt_lang: str
    duration_ms: float
    provider: str = "bhashini"


class NMTService:
    def __init__(self, bhashini_client: BhashiniClient, settings: Settings) -> None:
        self._bhashini = bhashini_client
        self._settings = settings

    async def translate(self, text: str, src_lang: str, tgt_lang: str) -> NMTResult:
        if not text or not text.strip():
            return NMTResult(translated_text="", src_lang=src_lang, tgt_lang=tgt_lang, duration_ms=0.0)

        start = time.perf_counter()
        try:
            response = await self._bhashini.translate(text, src_lang, tgt_lang)
            duration_ms = (time.perf_counter() - start) * 1000
            logger.info("nmt_success", provider="bhashini", duration_ms=duration_ms)
            return NMTResult(
                translated_text=response.translated_text,
                src_lang=src_lang,
                tgt_lang=tgt_lang,
                duration_ms=duration_ms,
                provider="bhashini",
            )
        except BhashiniAPIError as exc:
            logger.warning("nmt_bhashini_failed", error=str(exc))
            if self._settings.GOOGLE_TRANSLATE_KEY:
                return await self._translate_google(text, src_lang, tgt_lang, start)
            raise

    async def _translate_google(self, text: str, src_lang: str, tgt_lang: str, start: float) -> NMTResult:
        url = "https://translation.googleapis.com/language/translate/v2"
        params = {
            "key": self._settings.GOOGLE_TRANSLATE_KEY,
            "q": text,
            "source": src_lang,
            "target": tgt_lang,
            "format": "text",
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(url, params=params)
            response.raise_for_status()
            data = response.json()

        translated = data["data"]["translations"][0]["translatedText"]
        duration_ms = (time.perf_counter() - start) * 1000
        logger.info("nmt_success", provider="google", duration_ms=duration_ms)
        return NMTResult(
            translated_text=translated,
            src_lang=src_lang,
            tgt_lang=tgt_lang,
            duration_ms=duration_ms,
            provider="google",
        )
