"""Full STT -> NMT -> TTS pipeline for a completed utterance."""
from __future__ import annotations

import time

import structlog
from fastapi import WebSocket

from services.nmt_service import NMTService
from services.stt_service import STTService
from services.tts_service import TTSService
from session.session_manager import SessionManager, SessionState

logger = structlog.get_logger(__name__)

TTS_AUDIO_FLAG = bytes([0x03])


class Orchestrator:
    """Wires STT, NMT and TTS services into the end-to-end translation pipeline."""

    def __init__(self, stt_service: STTService, nmt_service: NMTService, tts_service: TTSService) -> None:
        self._stt = stt_service
        self._nmt = nmt_service
        self._tts = tts_service

    async def process_utterance(
        self,
        session: SessionState,
        pcm_data: bytes,
        src_lang: str,
        tgt_lang: str,
        websocket: WebSocket,
        session_manager: SessionManager | None = None,
    ) -> None:
        """Runs STT -> NMT -> TTS for one utterance, streaming results to the client
        as each stage completes rather than waiting for the whole pipeline.
        """
        pipeline_start = time.perf_counter()

        try:
            stt_result = await self._stt.transcribe(pcm_data, src_lang)
        except Exception as exc:  # noqa: BLE001 - surfaced to client, pipeline must not crash
            logger.error("pipeline_stt_failed", session_id=session.session_id, error=str(exc))
            await websocket.send_json(
                {"type": "error", "message": f"Speech recognition failed: {exc}"}
            )
            return

        stt_ms = (time.perf_counter() - pipeline_start) * 1000
        await websocket.send_json(
            {"type": "transcript", "text": stt_result.text, "final": True, "lang": src_lang}
        )

        if not stt_result.text.strip():
            logger.info("pipeline_empty_transcript", session_id=session.session_id)
            return

        nmt_start = time.perf_counter()
        try:
            nmt_result = await self._nmt.translate(stt_result.text, src_lang, tgt_lang)
        except Exception as exc:  # noqa: BLE001
            logger.error("pipeline_nmt_failed", session_id=session.session_id, error=str(exc))
            await websocket.send_json(
                {"type": "error", "message": f"Translation failed: {exc}"}
            )
            return
        nmt_ms = (time.perf_counter() - nmt_start) * 1000

        await websocket.send_json(
            {"type": "translation", "text": nmt_result.translated_text, "lang": tgt_lang}
        )

        if not nmt_result.translated_text.strip():
            return

        tts_start = time.perf_counter()
        tts_bytes = 0
        try:
            async for audio_chunk in self._tts.synthesize_stream(nmt_result.translated_text, tgt_lang):
                await websocket.send_bytes(TTS_AUDIO_FLAG + audio_chunk)
                tts_bytes += len(audio_chunk)
        except Exception as exc:  # noqa: BLE001
            logger.error("pipeline_tts_failed", session_id=session.session_id, error=str(exc))
            await websocket.send_json(
                {"type": "error", "message": f"Speech synthesis failed: {exc}"}
            )
            return
        tts_ms = (time.perf_counter() - tts_start) * 1000

        total_ms = (time.perf_counter() - pipeline_start) * 1000
        logger.info(
            "pipeline_complete",
            session_id=session.session_id,
            stt_ms=round(stt_ms, 1),
            nmt_ms=round(nmt_ms, 1),
            tts_ms=round(tts_ms, 1),
            total_ms=round(total_ms, 1),
            tts_bytes=tts_bytes,
        )

        if session_manager is not None:
            current = await session_manager.get_session(session.session_id) or session
            await session_manager.update_session(
                session.session_id,
                utterance_count=current.utterance_count + 1,
                total_audio_bytes=current.total_audio_bytes + len(pcm_data),
            )
