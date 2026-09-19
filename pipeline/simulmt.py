"""Simultaneous translation: feed partial STT text into NMT at clause boundaries
instead of waiting for the full utterance, shaving perceived latency.

This module is deliberately decoupled from audio/STT transport: it operates on
text fragments as they arrive (e.g. from incremental ASR calls over growing
audio buffers) and emits clauses to translate as soon as a boundary is found.
"""
from __future__ import annotations

import re

import structlog
from fastapi import WebSocket

from services.nmt_service import NMTService

logger = structlog.get_logger(__name__)

CLAUSE_BOUNDARY_PATTERN = re.compile(r"[.!?,;।]")
MIN_WORDS_FOR_CLAUSE = 5


class SimulMTProcessor:
    """Tracks partial transcript text for one utterance and triggers early,
    incremental translation of completed clauses.
    """

    def __init__(self, nmt_service: NMTService, src_lang: str, tgt_lang: str) -> None:
        self._nmt = nmt_service
        self._src_lang = src_lang
        self._tgt_lang = tgt_lang
        self._translated_prefix = ""
        self._pending_text = ""
        self._consumed_chars = 0

    async def on_partial_transcript(self, full_partial_text: str, websocket: WebSocket) -> None:
        """Call with the cumulative partial transcript so far (not a delta).
        Detects any newly completed clause and, if found, translates and sends
        it immediately as a translation JSON frame.
        """
        new_text = full_partial_text[self._consumed_chars :]
        if not new_text:
            return

        clause, remainder = self._extract_clause(new_text)
        if clause is None:
            return

        self._consumed_chars += len(clause)
        translated = await self._translate_clause(clause)
        if translated:
            self._translated_prefix = f"{self._translated_prefix} {translated}".strip()
            await websocket.send_json(
                {"type": "translation", "text": self._translated_prefix, "lang": self._tgt_lang}
            )

    async def finalize(self, full_final_text: str, websocket: WebSocket) -> str:
        """Call once the utterance is complete. Translates the full text and
        sends it as the authoritative correction, replacing any partial
        translations sent during the utterance.
        """
        result = await self._nmt.translate(full_final_text, self._src_lang, self._tgt_lang)
        await websocket.send_json(
            {"type": "translation", "text": result.translated_text, "lang": self._tgt_lang}
        )
        return result.translated_text

    def _extract_clause(self, text: str) -> tuple[str | None, str]:
        match = CLAUSE_BOUNDARY_PATTERN.search(text)
        if not match:
            return None, text

        clause = text[: match.end()]
        word_count = len(clause.split())
        if word_count < MIN_WORDS_FOR_CLAUSE:
            return None, text

        return clause, text[match.end() :]

    async def _translate_clause(self, clause: str) -> str:
        try:
            result = await self._nmt.translate(clause, self._src_lang, self._tgt_lang)
            return result.translated_text
        except Exception as exc:  # noqa: BLE001 - partial translation is best-effort
            logger.warning("simulmt_clause_translate_failed", error=str(exc))
            return ""
