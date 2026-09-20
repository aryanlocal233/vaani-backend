"""Real STT/NMT/TTS translation server backed by Sarvam AI, using the same
binary WebSocket protocol as mock_server.py.

Run:
    python -m uvicorn sarvam_server:app --host 0.0.0.0 --port 8000 --reload

Requires SARVAM_API_KEY in the environment or a .env file (see .env.example).
Never commit a real key to source -- this module only ever reads it from
the environment.
"""
from __future__ import annotations

import base64
import logging
import os
import struct
import sys
import time

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

# On Windows, stdout defaults to the console's codepage (often cp1252) in
# strict error mode, which raises UnicodeEncodeError on Devanagari/Tamil/etc.
# transcripts -- crashing the pipeline mid-utterance right after a successful
# transcription, before any response reaches the client. stderr already
# defaults to errors='backslashreplace' (why logger.* calls survive this),
# so make stdout equally tolerant instead of crashing on real STT output.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("vaani.sarvam")

SARVAM_API_KEY = os.environ.get("SARVAM_API_KEY", "")
if not SARVAM_API_KEY:
    logger.warning(
        "SARVAM_API_KEY is not set. Set it in your environment or in a .env file "
        "(see .env.example) before running this server -- every pipeline call will fail."
    )

SARVAM_BASE_URL = "https://api.sarvam.ai"
STT_URL = f"{SARVAM_BASE_URL}/speech-to-text"
TRANSLATE_URL = f"{SARVAM_BASE_URL}/translate"
TTS_URL = f"{SARVAM_BASE_URL}/text-to-speech"

app = FastAPI(title="Vaani Sarvam AI Translation Server")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

http_client = httpx.AsyncClient(timeout=30.0, limits=httpx.Limits(max_connections=50))

SUPPORTED_LANGUAGES = {
    "hi": "Hindi", "ta": "Tamil", "te": "Telugu", "bn": "Bengali",
    "kn": "Kannada", "mr": "Marathi", "gu": "Gujarati", "pa": "Punjabi",
    "ml": "Malayalam", "or": "Odia", "as": "Assamese", "en": "English",
}

# Short ISO code -> Sarvam BCP-47 code. Verified against Sarvam's actual
# accepted-values list (returned in its own 400 error responses) for all 12
# languages -- every code here is confirmed, not guessed. Note Odia is
# "od-IN" in Sarvam's API, not the ISO 639-1 "or-IN" you'd expect.
LANGUAGE_CODE_MAP = {
    "hi": "hi-IN",
    "ta": "ta-IN",
    "te": "te-IN",
    "bn": "bn-IN",
    "kn": "kn-IN",
    "mr": "mr-IN",
    "gu": "gu-IN",
    "pa": "pa-IN",
    "ml": "ml-IN",
    "or": "od-IN",
    "as": "as-IN",
    "en": "en-IN",
}

# The speaker names originally specified (meera, anushka, arvind, ...) belong
# to an older bulbul model version and are rejected by the current one
# (bulbul:v3). These are valid bulbul:v3 speaker names instead; the
# language->speaker pairing here is an arbitrary pick from the API's valid
# list, not a vetted per-language/accent match -- worth listening-testing
# and adjusting once you have real audio output to judge.
DEFAULT_SPEAKER = "priya"
SPEAKER_MAP = {
    "hi-IN": "priya",
    "ta-IN": "kavya",
    "te-IN": "aditya",
    "bn-IN": "ishita",
    "kn-IN": "rahul",
    "mr-IN": "shruti",
    "en-IN": "priya",
}

FLAG_HEARTBEAT = 0x00
FLAG_AUDIO_CHUNK = 0x01
FLAG_END_OF_UTTERANCE = 0x02
FLAG_TTS_AUDIO = 0x03

CLOSE_CODE_INVALID_LANGUAGE = 4008
TTS_CHUNK_SIZE = 4096

WAV_HEADER_SIZE = 44


class SarvamAPIError(Exception):
    """Raised when a Sarvam API call fails or returns an unexpected shape."""


def pcm_to_wav(pcm_bytes: bytes, sample_rate: int = 16000, channels: int = 1, bits_per_sample: int = 16) -> bytes:
    """Wraps raw PCM16 mono audio in a canonical 44-byte WAV header.
    Sarvam's speech-to-text endpoint expects a WAV file, but the Android
    client only ever sends raw PCM over the WebSocket.
    """
    byte_rate = sample_rate * channels * bits_per_sample // 8
    block_align = channels * bits_per_sample // 8
    data_size = len(pcm_bytes)

    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_size,
        b"WAVE",
        b"fmt ",
        16,
        1,  # PCM format
        channels,
        sample_rate,
        byte_rate,
        block_align,
        bits_per_sample,
        b"data",
        data_size,
    )
    return header + pcm_bytes


def wav_to_pcm(wav_bytes: bytes) -> bytes:
    """Strips the leading 44-byte canonical WAV header, returning raw PCM.
    Sarvam TTS responses are canonical (no extra chunks before `data`), so a
    fixed offset is sufficient here.
    """
    return wav_bytes[WAV_HEADER_SIZE:]


async def sarvam_stt(pcm_data: bytes, src_lang: str) -> str:
    language_code = LANGUAGE_CODE_MAP.get(src_lang, f"{src_lang}-IN")
    wav_bytes = pcm_to_wav(pcm_data)

    logger.info("[STT] Sending %d bytes of audio...", len(pcm_data))
    start = time.perf_counter()

    try:
        response = await http_client.post(
            STT_URL,
            headers={"api-subscription-key": SARVAM_API_KEY},
            files={"file": ("audio.wav", wav_bytes, "audio/wav")},
            data={"model": "saaras:v3", "language_code": language_code},
        )
        response.raise_for_status()
        body = response.json()
        print(f"[STT] Full response: {body}", flush=True)
    except httpx.HTTPStatusError as exc:
        raise SarvamAPIError(f"STT request failed: {exc.response.status_code} {exc.response.text}") from exc
    except httpx.HTTPError as exc:
        raise SarvamAPIError(f"STT request failed: {exc}") from exc

    transcript = body.get("transcript", "")
    duration_ms = (time.perf_counter() - start) * 1000
    logger.info('[STT] Transcript: "%s" (%.0fms)', transcript, duration_ms)
    return transcript


async def sarvam_translate(text: str, src_lang: str, tgt_lang: str) -> str:
    source_code = LANGUAGE_CODE_MAP.get(src_lang, f"{src_lang}-IN")
    target_code = LANGUAGE_CODE_MAP.get(tgt_lang, f"{tgt_lang}-IN")

    logger.info("[NMT] Translating %s->%s...", src_lang, tgt_lang)
    start = time.perf_counter()

    # mayura:v1 rejects Assamese outright ("not supported in mayura:v1"); Sarvam's
    # own error names sarvam-translate:v1 as the model to use for it instead. Kept
    # as a targeted exception rather than switching everyone to it, since mayura:v1
    # is confirmed working for the other 9 languages already.
    model = "sarvam-translate:v1" if "as-IN" in (source_code, target_code) else "mayura:v1"

    payload = {
        "input": text,
        "source_language_code": source_code,
        "target_language_code": target_code,
        "speaker_gender": "Female",
        "mode": "formal",
        "model": model,
        "enable_preprocessing": True,
    }

    try:
        response = await http_client.post(
            TRANSLATE_URL,
            headers={
                "api-subscription-key": SARVAM_API_KEY,
                "Content-Type": "application/json",
            },
            json=payload,
        )
        response.raise_for_status()
        body = response.json()
    except httpx.HTTPStatusError as exc:
        raise SarvamAPIError(f"Translate request failed: {exc.response.status_code} {exc.response.text}") from exc
    except httpx.HTTPError as exc:
        raise SarvamAPIError(f"Translate request failed: {exc}") from exc

    translated_text = body.get("translated_text", "")
    duration_ms = (time.perf_counter() - start) * 1000
    logger.info('[NMT] Result: "%s" (%.0fms)', translated_text, duration_ms)
    return translated_text


async def sarvam_tts(text: str, tgt_lang: str) -> bytes:
    target_code = LANGUAGE_CODE_MAP.get(tgt_lang, f"{tgt_lang}-IN")
    speaker = SPEAKER_MAP.get(target_code, DEFAULT_SPEAKER)

    logger.info("[TTS] Synthesizing %s audio...", SUPPORTED_LANGUAGES.get(tgt_lang, tgt_lang))
    start = time.perf_counter()

    payload = {
        "inputs": [text],
        "target_language_code": target_code,
        "speaker": speaker,
        "pitch": 0,
        "pace": 1.0,
        "loudness": 1.5,
        "speech_sample_rate": 16000,
        "enable_preprocessing": True,
        "model": "bulbul:v3",
    }

    try:
        response = await http_client.post(
            TTS_URL,
            headers={
                "api-subscription-key": SARVAM_API_KEY,
                "Content-Type": "application/json",
            },
            json=payload,
        )
        response.raise_for_status()
        body = response.json()
    except httpx.HTTPStatusError as exc:
        raise SarvamAPIError(f"TTS request failed: {exc.response.status_code} {exc.response.text}") from exc
    except httpx.HTTPError as exc:
        raise SarvamAPIError(f"TTS request failed: {exc}") from exc

    audios = body.get("audios") or []
    if not audios:
        raise SarvamAPIError(f"TTS response contained no audio: {body}")

    wav_bytes = base64.b64decode(audios[0])
    pcm_bytes = wav_to_pcm(wav_bytes)

    duration_ms = (time.perf_counter() - start) * 1000
    logger.info("[TTS] Got %d bytes PCM, sending in chunks (%.0fms)", len(pcm_bytes), duration_ms)
    return pcm_bytes


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "mode": "sarvam", "api_key_configured": bool(SARVAM_API_KEY)}


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
                "session_id": "sarvam-session",
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
                final_pcm = bytes(audio_buffer)
                audio_buffer.clear()

                if len(final_pcm) == 0:
                    logger.info("End-of-utterance received with no audio buffered; skipping pipeline")
                    continue

                utterance_count += 1
                logger.info(
                    "End-of-utterance received (%d bytes buffered) -> running Sarvam pipeline #%d",
                    len(final_pcm),
                    utterance_count,
                )
                await run_sarvam_pipeline(websocket, final_pcm, src_lang, tgt_lang)

            else:
                logger.warning("Unknown flag byte: 0x%02x", flag)

    except WebSocketDisconnect:
        logger.info("Client disconnected (WebSocketDisconnect)")
    except Exception as e:  # noqa: BLE001 - never crash the WS loop
        print(f"[ERROR] WebSocket error: {e}")
        logger.exception("Unexpected error in WebSocket handler")
    finally:
        duration_s = time.time() - session_start
        logger.info(
            "Session closed | duration=%.2fs | utterances=%d",
            duration_s,
            utterance_count,
        )


async def run_sarvam_pipeline(websocket: WebSocket, pcm_data: bytes, src_lang: str, tgt_lang: str) -> None:
    """Runs real STT -> NMT -> TTS via Sarvam AI, streaming each stage's
    result to the client as soon as it's ready. Any failure sends an error
    JSON frame and returns -- never raises out of this function.
    """
    try:
        transcript = await sarvam_stt(pcm_data, src_lang)
    except SarvamAPIError as exc:
        logger.error("STT failed: %s", exc)
        await websocket.send_json({"type": "error", "message": f"Speech recognition failed: {exc}"})
        return

    if not transcript.strip():
        logger.warning("STT returned empty transcript; sending no_speech and skipping NMT/TTS")
        await websocket.send_json({"type": "no_speech", "message": "No speech detected"})
        return

    await websocket.send_json({"type": "transcript", "text": transcript, "final": True, "lang": src_lang})

    try:
        translated_text = await sarvam_translate(transcript, src_lang, tgt_lang)
    except SarvamAPIError as exc:
        logger.error("Translation failed: %s", exc)
        await websocket.send_json({"type": "error", "message": f"Translation failed: {exc}"})
        return

    if not translated_text.strip():
        logger.warning("NMT returned empty translation; aborting pipeline")
        await websocket.send_json({"type": "error", "message": "Translation returned empty result"})
        return

    await websocket.send_json({"type": "translation", "text": translated_text, "lang": tgt_lang})

    try:
        pcm_audio = await sarvam_tts(translated_text, tgt_lang)
    except SarvamAPIError as exc:
        logger.error("TTS failed: %s", exc)
        await websocket.send_json({"type": "error", "message": f"Speech synthesis failed: {exc}"})
        return

    chunk_count = 0
    for offset in range(0, len(pcm_audio), TTS_CHUNK_SIZE):
        chunk = pcm_audio[offset : offset + TTS_CHUNK_SIZE]
        await websocket.send_bytes(bytes([FLAG_TTS_AUDIO]) + chunk)
        chunk_count += 1

    logger.info("Pipeline complete: sent %d audio chunks (%d bytes)", chunk_count, len(pcm_audio))


@app.on_event("shutdown")
async def shutdown_event() -> None:
    await http_client.aclose()
