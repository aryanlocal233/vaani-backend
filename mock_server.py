"""Standalone mock translation server for end-to-end Android pipeline testing.

Simulates the full STT -> NMT -> TTS pipeline without any real Bhashini
credentials: fixed Hindi transcript, fixed Tamil translation, and a real
440Hz sine-wave PCM tone streamed back as "TTS audio". Lets you verify the
Android app's WebSocket protocol handling, UI state transitions, and audio
playback path in isolation from the actual translation backend.

Run:
    uvicorn mock_server:app --host 0.0.0.0 --port 8000 --reload

Dependencies: fastapi, uvicorn, websockets (all already in requirements.txt).
"""
from __future__ import annotations

import asyncio
import logging
import math
import struct
import time

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("vaani.mock")

app = FastAPI(title="Vaani Mock Translation Server")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SUPPORTED_LANGUAGES = {
    "hi": "Hindi", "ta": "Tamil", "te": "Telugu", "bn": "Bengali",
    "kn": "Kannada", "mr": "Marathi", "gu": "Gujarati", "pa": "Punjabi",
    "ml": "Malayalam", "or": "Odia", "as": "Assamese", "en": "English",
}

FLAG_HEARTBEAT = 0x00
FLAG_AUDIO_CHUNK = 0x01
FLAG_END_OF_UTTERANCE = 0x02
FLAG_TTS_AUDIO = 0x03

CLOSE_CODE_INVALID_LANGUAGE = 4008

TTS_CHUNK_SIZE = 4096
SAMPLE_RATE = 16000
TONE_FREQUENCY_HZ = 440
TONE_DURATION_S = 1.0

MOCK_TRANSCRIPT_HI = "नमस्ते आप कैसे हैं"
MOCK_TRANSLATION_TA = "வணக்கம் நீங்கள் எப்படி இருக்கிறீர்கள்"

STT_LATENCY_S = 0.3
NMT_LATENCY_S = 0.2
TTS_LATENCY_S = 0.15


def generate_sine_wave_pcm(
    frequency: int = TONE_FREQUENCY_HZ,
    duration_s: float = TONE_DURATION_S,
    sample_rate: int = SAMPLE_RATE,
) -> bytes:
    """Generates a real PCM16 mono sine wave tone -- proof that raw audio
    bytes actually flow end-to-end through the pipeline and the Android
    AudioTrack playback path, not just placeholder silence.
    """
    sample_count = int(sample_rate * duration_s)
    samples = [
        int(32767 * math.sin(2 * math.pi * frequency * i / sample_rate))
        for i in range(sample_count)
    ]
    return struct.pack(f"{len(samples)}h", *samples)


# Generated once at import time; the tone is identical for every utterance.
MOCK_TONE_PCM = generate_sine_wave_pcm()


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "mode": "mock"}


@app.websocket("/ws/translate/{src_lang}/{tgt_lang}")
async def websocket_translate(websocket: WebSocket, src_lang: str, tgt_lang: str) -> None:
    session_start = time.time()
    utterance_count = 0
    audio_buffer = bytearray()

    try:
        if src_lang not in SUPPORTED_LANGUAGES or tgt_lang not in SUPPORTED_LANGUAGES:
            logger.warning("Rejecting connection: unsupported language pair %s->%s", src_lang, tgt_lang)
            await websocket.close(code=CLOSE_CODE_INVALID_LANGUAGE, reason="Unsupported language")
            return

        await websocket.accept()
        client = f"{websocket.client.host}:{websocket.client.port}" if websocket.client else "unknown"
        logger.info("Client connected from %s | %s -> %s", client, src_lang, tgt_lang)

        await websocket.send_json(
            {
                "type": "connected",
                "session_id": "mock-session",
                "src_lang": src_lang,
                "tgt_lang": tgt_lang,
            }
        )

        while True:
            message = await websocket.receive()

            if message["type"] == "websocket.disconnect":
                break

            data = message.get("bytes")
            if data is None or not data:
                continue

            flag = data[0]
            payload = data[1:]

            if flag == FLAG_HEARTBEAT:
                logger.info("Heartbeat received -> sending pong")
                await websocket.send_bytes(bytes([FLAG_HEARTBEAT]))

            elif flag == FLAG_AUDIO_CHUNK:
                audio_buffer.extend(payload)
                logger.info(
                    "Audio chunk buffered: +%d bytes (total %d bytes)",
                    len(payload),
                    len(audio_buffer),
                )

            elif flag == FLAG_END_OF_UTTERANCE:
                audio_buffer.extend(payload)
                total_bytes = len(audio_buffer)
                audio_buffer.clear()

                if total_bytes == 0:
                    logger.info("End-of-utterance received with no audio buffered; skipping pipeline")
                    continue

                utterance_count += 1
                logger.info(
                    "End-of-utterance received (%d bytes buffered) -> running mock pipeline #%d",
                    total_bytes,
                    utterance_count,
                )
                await run_mock_pipeline(websocket, src_lang, tgt_lang)

            else:
                logger.warning("Unknown flag byte: 0x%02x", flag)

    except WebSocketDisconnect:
        logger.info("Client disconnected (WebSocketDisconnect)")
    except Exception as e:
        print(f"[ERROR] WebSocket error: {e}")
        logger.exception("Unexpected error in WebSocket handler")
    finally:
        duration_s = time.time() - session_start
        logger.info(
            "Session closed | duration=%.2fs | utterances=%d",
            duration_s,
            utterance_count,
        )


async def run_mock_pipeline(websocket: WebSocket, src_lang: str, tgt_lang: str) -> None:
    """Simulates STT -> NMT -> TTS with realistic latencies and fixed output,
    streaming results to the client exactly as the real orchestrator does.
    """
    pipeline_start = time.perf_counter()

    # --- Step 1-2: mock STT ---
    logger.info("[STT] transcribing... (simulated %dms)", int(STT_LATENCY_S * 1000))
    await asyncio.sleep(STT_LATENCY_S)
    transcript_msg = {
        "type": "transcript",
        "text": MOCK_TRANSCRIPT_HI,
        "final": True,
        "lang": src_lang,
    }
    await websocket.send_json(transcript_msg)
    logger.info("[STT] sent transcript: %s", transcript_msg)

    # --- Step 3-4: mock NMT ---
    logger.info("[NMT] translating... (simulated %dms)", int(NMT_LATENCY_S * 1000))
    await asyncio.sleep(NMT_LATENCY_S)
    translation_msg = {
        "type": "translation",
        "text": MOCK_TRANSLATION_TA,
        "lang": tgt_lang,
    }
    await websocket.send_json(translation_msg)
    logger.info("[NMT] sent translation: %s", translation_msg)

    # --- Step 5-6: mock TTS ---
    logger.info("[TTS] synthesizing... (simulated %dms)", int(TTS_LATENCY_S * 1000))
    await asyncio.sleep(TTS_LATENCY_S)

    chunk_count = 0
    bytes_sent = 0
    for offset in range(0, len(MOCK_TONE_PCM), TTS_CHUNK_SIZE):
        chunk = MOCK_TONE_PCM[offset : offset + TTS_CHUNK_SIZE]
        await websocket.send_bytes(bytes([FLAG_TTS_AUDIO]) + chunk)
        chunk_count += 1
        bytes_sent += len(chunk)

    total_ms = (time.perf_counter() - pipeline_start) * 1000
    logger.info(
        "[TTS] sent %d PCM chunks (%d bytes, %.1fHz tone, %.1fs) | pipeline total=%.1fms",
        chunk_count,
        bytes_sent,
        TONE_FREQUENCY_HZ,
        TONE_DURATION_S,
        total_ms,
    )
