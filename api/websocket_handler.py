"""WebSocket endpoint implementing the binary Vaani translation protocol.

Client -> Server (binary frames):
  byte[0] = 0x00  heartbeat (no payload)
  byte[0] = 0x01  audio chunk (payload = raw PCM16 mono 16kHz)
  byte[0] = 0x02  end of utterance (payload = final PCM chunk)

Server -> Client:
  binary: byte[0] = 0x03 + raw PCM bytes (TTS audio)
  text JSON: {"type": "connected", ...}
  text JSON: {"type": "transcript", "text": "...", "final": bool, "lang": "hi"}
  text JSON: {"type": "translation", "text": "...", "lang": "ta"}
  text JSON: {"type": "error", "message": "..."}
"""
from __future__ import annotations

import asyncio
import time

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from audio.audio_buffer import AudioBuffer
from audio.audio_utils import bytes_for_duration_ms
from pipeline.simulmt import SimulMTProcessor

logger = structlog.get_logger(__name__)

router = APIRouter()

SUPPORTED_LANGUAGES = {
    "hi": "Hindi",
    "ta": "Tamil",
    "te": "Telugu",
    "bn": "Bengali",
    "kn": "Kannada",
    "mr": "Marathi",
    "gu": "Gujarati",
    "pa": "Punjabi",
    "ml": "Malayalam",
    "or": "Odia",
    "as": "Assamese",
    "en": "English",
}

FLAG_HEARTBEAT = 0x00
FLAG_AUDIO_CHUNK = 0x01
FLAG_END_OF_UTTERANCE = 0x02
FLAG_TTS_AUDIO = 0x03

SIMULMT_TRIGGER_MS = 200.0
HEARTBEAT_TIMEOUT_SECONDS = 30.0

CLOSE_CODE_INVALID_LANGUAGE = 4008


async def _safe_send_json(websocket: WebSocket, payload: dict) -> None:
    if websocket.application_state == WebSocketState.CONNECTED:
        await websocket.send_json(payload)


@router.websocket("/ws/translate/{src_lang}/{tgt_lang}")
async def websocket_translate(websocket: WebSocket, src_lang: str, tgt_lang: str) -> None:
    if src_lang not in SUPPORTED_LANGUAGES or tgt_lang not in SUPPORTED_LANGUAGES:
        await websocket.close(code=CLOSE_CODE_INVALID_LANGUAGE, reason="Unsupported language")
        return

    await websocket.accept()

    session_manager = websocket.app.state.session_manager
    orchestrator = websocket.app.state.orchestrator
    nmt_service = websocket.app.state.nmt_service

    session = await session_manager.create_session(src_lang, tgt_lang)
    audio_buffer = AudioBuffer(sample_rate=websocket.app.state.settings.AUDIO_SAMPLE_RATE)
    simulmt = SimulMTProcessor(nmt_service, src_lang, tgt_lang)

    simulmt_state = {"last_bytes": 0}
    simulmt_trigger_bytes = bytes_for_duration_ms(
        SIMULMT_TRIGGER_MS, websocket.app.state.settings.AUDIO_SAMPLE_RATE
    )

    session_start = time.time()
    last_activity = time.time()
    stop_event = asyncio.Event()

    async def heartbeat_watchdog() -> None:
        nonlocal last_activity
        try:
            while not stop_event.is_set():
                await asyncio.sleep(1.0)
                if time.time() - last_activity > HEARTBEAT_TIMEOUT_SECONDS:
                    logger.warning("heartbeat_timeout", session_id=session.session_id)
                    await websocket.close(code=1001, reason="Heartbeat timeout")
                    stop_event.set()
                    return
        except asyncio.CancelledError:
            pass

    watchdog_task = asyncio.create_task(heartbeat_watchdog())

    try:
        await _safe_send_json(
            websocket,
            {
                "type": "connected",
                "session_id": session.session_id,
                "src_lang": src_lang,
                "tgt_lang": tgt_lang,
            },
        )

        while True:
            message = await websocket.receive()

            if message["type"] == "websocket.disconnect":
                break

            data = message.get("bytes")
            if data is None:
                # Ignore stray text frames from the client; protocol is binary-only.
                continue

            last_activity = time.time()

            try:
                await _handle_binary_message(
                    data,
                    websocket=websocket,
                    session=session,
                    session_manager=session_manager,
                    audio_buffer=audio_buffer,
                    simulmt=simulmt,
                    orchestrator=orchestrator,
                    trigger_bytes=simulmt_trigger_bytes,
                    simulmt_state=simulmt_state,
                )
            except Exception as exc:  # noqa: BLE001 - never crash the WS loop
                logger.error("ws_message_error", session_id=session.session_id, error=str(exc))
                await _safe_send_json(
                    websocket,
                    {
                        "type": "error",
                        "message": "Internal error processing message; please retry.",
                    },
                )

    except WebSocketDisconnect:
        pass
    finally:
        stop_event.set()
        watchdog_task.cancel()

        duration_s = time.time() - session_start
        final_session = await session_manager.get_session(session.session_id)
        utterance_count = final_session.utterance_count if final_session else session.utterance_count

        logger.info(
            "session_closed",
            session_id=session.session_id,
            duration_s=round(duration_s, 2),
            utterance_count=utterance_count,
        )
        await session_manager.delete_session(session.session_id)


async def _handle_binary_message(
    data: bytes,
    *,
    websocket: WebSocket,
    session,
    session_manager,
    audio_buffer: AudioBuffer,
    simulmt: SimulMTProcessor,
    orchestrator,
    trigger_bytes: int,
    simulmt_state: dict,
) -> None:
    if not data:
        return

    flag = data[0]
    payload = data[1:]

    if flag == FLAG_HEARTBEAT:
        await websocket.send_bytes(bytes([FLAG_HEARTBEAT]))
        return

    if flag == FLAG_AUDIO_CHUNK:
        audio_buffer.add_chunk(payload)
        if len(audio_buffer) - simulmt_state["last_bytes"] >= trigger_bytes:
            simulmt_state["last_bytes"] = len(audio_buffer)
            # SimulMT operates on partial STT text; the batch ASR call here stands
            # in for a streaming ASR partial-result callback.
            stt_service = websocket.app.state.stt_service
            try:
                partial = await stt_service.transcribe(audio_buffer.get_all(), session.src_lang)
                await _safe_send_json(
                    websocket,
                    {"type": "transcript", "text": partial.text, "final": False, "lang": session.src_lang},
                )
                await simulmt.on_partial_transcript(partial.text, websocket)
            except Exception as exc:  # noqa: BLE001 - partial results are best-effort
                logger.warning("simulmt_partial_failed", session_id=session.session_id, error=str(exc))
        return

    if flag == FLAG_END_OF_UTTERANCE:
        audio_buffer.add_chunk(payload)
        final_pcm = audio_buffer.flush()
        simulmt_state["last_bytes"] = 0

        if not final_pcm:
            return

        await orchestrator.process_utterance(
            session=session,
            pcm_data=final_pcm,
            src_lang=session.src_lang,
            tgt_lang=session.tgt_lang,
            websocket=websocket,
            session_manager=session_manager,
        )
        return

    logger.warning("ws_unknown_flag", flag=flag, session_id=session.session_id)
