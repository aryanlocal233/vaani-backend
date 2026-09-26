"""Real STT/NMT/TTS translation server backed by Sarvam AI, using the same
binary WebSocket protocol as mock_server.py.

Run:
    python -m uvicorn sarvam_server:app --host 0.0.0.0 --port 8000 --reload

Requires SARVAM_API_KEY in the environment or a .env file (see .env.example).
Never commit a real key to source -- this module only ever reads it from
the environment.
"""
from __future__ import annotations

import array
import asyncio
import base64
import logging
import os
import re
import struct
import sys
import time

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

import admin
import db
import faq_state
import pack_config
import pricing
from pack_config import EVENT_PACK, SUPPORTED_LANGUAGES

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

if not pack_config.SESSION_SECRET:
    raise RuntimeError(
        "SESSION_SECRET is not set -- required to sign the admin panel's session cookie. "
        "Set it in .env (any long random string)."
    )
app.add_middleware(SessionMiddleware, secret_key=pack_config.SESSION_SECRET, same_site="lax")
app.include_router(admin.router)

http_client = httpx.AsyncClient(timeout=30.0, limits=httpx.Limits(max_connections=50))

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
TTS_CHUNK_SIZE = 8192

# Mela help-desk FAQ answers, loaded at startup from Postgres for EVENT_PACK (db.py) -- this is
# what turns Vaani from a plain translator into a help-desk system: a pilgrim's question that
# matches one of these gets answered instantly, in their own language, without a round trip
# through NMT or a live TTS call -- both cheaper (no per-character translate/TTS billing for
# repeat questions) and near-instant (no Sarvam API latency at all) compared to the full
# STT->NMT->TTS pipeline every other utterance goes through.
#
# Matching is a plain substring/keyword check per detected language, not semantic search yet --
# deliberately simple for a first version covering the handful of questions pilgrims actually
# repeat at a mela counter. Extend a pack by adding rows via migrate_faq.py (or a future admin
# API), not by editing this file.
def faq_voice_for(lang: str) -> str:
    bcp47 = LANGUAGE_CODE_MAP.get(lang, f"{lang}-IN")
    return SPEAKER_MAP.get(bcp47, DEFAULT_SPEAKER)


async def get_faq_audio(entry: dict, lang: str, text: str) -> bytes:
    """Durable audio cache: checks Postgres/disk first (survives container restarts), only
    calls Sarvam TTS on a true first-ever synthesis of this (pack, faq, language, voice)."""
    voice = faq_voice_for(lang)
    cached_path = await db.get_cached_audio_path(EVENT_PACK, entry["id"], lang, voice)
    if cached_path is not None:
        cached_bytes = db.read_audio_file(cached_path)
        if cached_bytes is not None:
            return cached_bytes

    pcm = await sarvam_tts(text, lang)
    pcm = apply_edge_fade(pcm)
    await db.save_audio_cache(EVENT_PACK, entry["id"], lang, voice, pcm)
    return pcm


WAV_HEADER_SIZE = 44

# Splits on Latin sentence punctuation and the Devanagari-family danda/double-danda (used by
# several of our supported scripts -- Hindi, Marathi, Bengali, Odia, Assamese formal writing).
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?।॥])\s+")


def split_sentences(text: str) -> list[str]:
    """Splits translated text into sentence-ish chunks for TTS pipelining (see
    run_sarvam_pipeline). A single-sentence utterance -- the common case -- yields exactly one
    part, so this is a no-op for short exchanges; it only changes behavior for longer,
    multi-sentence responses.
    """
    parts = [p.strip() for p in SENTENCE_SPLIT_RE.split(text) if p.strip()]
    return parts or [text.strip()]


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


def apply_edge_fade(pcm_bytes: bytes, fade_ms: float = 5.0, sample_rate: int = 16000) -> bytes:
    """Ramps the first/last `fade_ms` of a PCM16 mono clip to/from silence.

    Each sentence in run_sarvam_pipeline is synthesized as an independent TTS call and their
    PCM outputs are concatenated back to back on one continuous AudioTrack stream client-side.
    Two separately-synthesized clips almost never meet at the same amplitude/phase, so joining
    them raw produces an audible click/pop at every sentence boundary -- this is what shows up
    as "crackling" on multi-sentence responses. A short (~5ms, inaudible as a fade but long
    enough to kill the discontinuity) linear ramp at each edge fixes that without needing a
    real crossfade between clips.
    """
    fade_samples = int(sample_rate * fade_ms / 1000)
    samples = array.array("h")
    samples.frombytes(pcm_bytes[: len(pcm_bytes) - (len(pcm_bytes) % 2)])
    total = len(samples)
    n = min(fade_samples, total // 2)
    if n <= 0:
        return pcm_bytes

    for i in range(n):
        samples[i] = int(samples[i] * (i / n))
        j = total - 1 - i
        samples[j] = int(samples[j] * (i / n))

    return samples.tobytes()


def wav_to_pcm(wav_bytes: bytes) -> bytes:
    """Locates the `data` chunk by walking the WAV's RIFF chunk list, returning
    its contents as raw PCM. A fixed 44-byte offset only holds for a WAV with
    exactly one `fmt ` chunk and nothing else before `data`; if Sarvam's
    encoder ever emits extra chunks (LIST/fact/etc.) ahead of `data`, a fixed
    offset would slice into real audio, or include trailing header bytes as
    if they were samples -- either shows up as crackling/noise on playback.
    """
    offset = 12  # skip the 12-byte RIFF/WAVE header ("RIFF" + size + "WAVE")
    while offset + 8 <= len(wav_bytes):
        chunk_id = wav_bytes[offset : offset + 4]
        chunk_size = struct.unpack_from("<I", wav_bytes, offset + 4)[0]
        data_start = offset + 8
        if chunk_id == b"data":
            return wav_bytes[data_start : data_start + chunk_size]
        # Per the RIFF spec, chunks are padded to an even byte count.
        offset = data_start + chunk_size + (chunk_size & 1)

    logger.warning("wav_to_pcm: no 'data' chunk found, falling back to fixed 44-byte offset")
    return wav_bytes[WAV_HEADER_SIZE:]


REVERSE_LANGUAGE_CODE_MAP = {bcp47: short for short, bcp47 in LANGUAGE_CODE_MAP.items()}

# Any of these keys, if present in a Sarvam STT response, is treated as the
# model's detected-language field. Kept as a list (checked in order) rather
# than a single hardcoded key because Sarvam's auto-detect response shape
# isn't confirmed from docs alone -- the [STT] Full response log line below
# should be used to verify which key is actually present before relying on
# this in production, and this list extended if it's something else.
DETECTED_LANGUAGE_KEYS = ("language_code", "detected_language_code", "lang_code")


async def sarvam_stt(pcm_data: bytes, language_code: str) -> tuple[str, str | None]:
    """Transcribes audio via Sarvam's saaras STT.

    `language_code` may be a specific BCP-47 code (forces that language) or
    "unknown" to have Sarvam auto-detect which language was actually spoken --
    saaras supports this across all languages in SUPPORTED_LANGUAGES. Returns
    (transcript, detected_language_code); detected_language_code is None if
    the response didn't include one (e.g. a forced, non-"unknown" call).
    """
    wav_bytes = pcm_to_wav(pcm_data)

    logger.info("[STT] Sending %d bytes of audio (language_code=%s)...", len(pcm_data), language_code)
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
    detected_code = next((body[key] for key in DETECTED_LANGUAGE_KEYS if body.get(key)), None)
    duration_ms = (time.perf_counter() - start) * 1000
    logger.info(
        '[STT] Transcript: "%s" (detected=%s, %.0fms)', transcript, detected_code, duration_ms
    )
    return transcript, detected_code


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
        # Measured directly against the live API: at 1.5 (the original setting), peaks sat at
        # 85-92% of int16 full scale across languages/exclamatory text -- not clipped by Sarvam
        # itself, but with almost no headroom left for anything downstream (phone media-stream
        # loudness enhancers, DRC, the speaker amp) before it clips there instead. Note this
        # parameter is NOT a simple linear gain -- 1.0 measured a *higher* peak than 1.5 in a
        # side-by-side test, so don't assume proportionality if retuning this. 0.7 measured
        # ~60% FS, giving real margin; if it's too quiet in a real mela, raise device media
        # volume rather than this value (lossless) rather than eating into headroom again.
        "loudness": 0.7,
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


@app.on_event("startup")
async def startup_event() -> None:
    await db.init_pool()
    await faq_state.reload()


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok", "mode": "sarvam", "event_pack": EVENT_PACK,
        "faq_count": len(faq_state.FAQ_ENTRIES), "api_key_configured": bool(SARVAM_API_KEY),
    }


@app.websocket("/ws/translate/{src_lang}/{tgt_lang}")
async def websocket_translate(websocket: WebSocket, src_lang: str, tgt_lang: str) -> None:
    session_start = time.time()
    utterance_count = 0
    audio_buffer = bytearray()
    # Optional query param so a future multi-counter deployment can tag analytics rows by
    # physical help-desk location without any protocol/client change (?counter_id=C-14).
    counter_id = websocket.query_params.get("counter_id", "unknown")

    try:
        if src_lang not in SUPPORTED_LANGUAGES or tgt_lang not in SUPPORTED_LANGUAGES:
            logger.warning("Rejecting connection: unsupported language pair %s->%s", src_lang, tgt_lang)
            await websocket.close(code=CLOSE_CODE_INVALID_LANGUAGE, reason="Unsupported language")
            return

        await websocket.accept()
        client = f"{websocket.client.host}:{websocket.client.port}" if websocket.client else "unknown"
        logger.info("Client connected from %s | %s -> %s (counter=%s)", client, src_lang, tgt_lang, counter_id)

        await websocket.send_json(
            {
                "type": "connected",
                "session_id": "sarvam-session",
                "src_lang": src_lang,
                "tgt_lang": tgt_lang,
            }
        )

        # src_lang (the operator/help-desk side, dropdown 1 in the app) is fixed for the whole
        # session. visitor_lang (dropdown 2, the public-facing side) starts as whatever the
        # session URL said, but is free to change to *any* supported language the moment
        # someone speaks one that isn't src_lang -- see run_sarvam_pipeline.
        visitor_lang = tgt_lang

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
                # visitor_lang is deliberately reassigned here: the operator's language
                # (src_lang) is fixed for the session, but whichever language the *other*
                # person is actually detected speaking becomes the new visitor_lang for
                # every subsequent utterance -- see run_sarvam_pipeline's docstring.
                visitor_lang = await run_sarvam_pipeline(websocket, final_pcm, src_lang, visitor_lang, counter_id)

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


async def run_sarvam_pipeline(
    websocket: WebSocket, pcm_data: bytes, operator_lang: str, visitor_lang: str, counter_id: str = "unknown"
) -> str:
    """Runs real STT -> NMT -> TTS via Sarvam AI, streaming each stage's
    result to the client as soon as it's ready. Any failure sends an error
    JSON frame and returns -- never raises out of this function.

    operator_lang (help-desk side) is fixed for the whole session. visitor_lang
    (public-facing side) is not a fixed pair partner -- it's just the most
    recently detected language for that side, and this function returns the
    (possibly updated) value for the caller to carry into the next utterance.
    STT runs in full auto-detect mode ("unknown"), so if the detected language
    is any supported language other than operator_lang, that becomes the new
    visitor_lang -- a session opened assuming Hindi can seamlessly pick up a
    Tamil-speaking pilgrim next, without the client reconnecting or having
    pre-selected Tamil.
    """
    pipeline_start = time.perf_counter()
    # Raw PCM16 mono @ 16kHz -- 32 bytes/ms, so this is exact audio duration, not a guess.
    audio_duration_ms = len(pcm_data) // 32

    stt_start = time.perf_counter()
    try:
        transcript, detected_bcp47 = await sarvam_stt(pcm_data, "unknown")
    except SarvamAPIError as exc:
        logger.error("STT failed: %s", exc)
        await websocket.send_json({"type": "error", "message": f"Speech recognition failed: {exc}"})
        # No successful API call was made, so nothing was billed for this request.
        await db.log_analytics(
            EVENT_PACK, counter_id, None, None, "none", False, False,
            None, int((time.perf_counter() - pipeline_start) * 1000),
        )
        return visitor_lang
    stt_ms = int((time.perf_counter() - stt_start) * 1000)
    # STT succeeded, so this cost is real regardless of what happens downstream -- every
    # remaining exit path below includes it.
    stt_cost_val = pricing.stt_cost(audio_duration_ms)

    if not transcript.strip():
        logger.warning("STT returned empty transcript; sending no_speech and skipping NMT/TTS")
        await websocket.send_json({"type": "no_speech", "message": "No speech detected"})
        await db.log_analytics(
            EVENT_PACK, counter_id, detected_bcp47, None, "none", False, False,
            stt_ms, int((time.perf_counter() - pipeline_start) * 1000),
            audio_duration_ms=audio_duration_ms, stt_cost_inr=stt_cost_val,
        )
        return visitor_lang

    detected_short = REVERSE_LANGUAGE_CODE_MAP.get(detected_bcp47 or "")
    if detected_short == operator_lang:
        actual_src, actual_tgt = operator_lang, visitor_lang
    elif detected_short is not None and detected_short in SUPPORTED_LANGUAGES:
        # Someone spoke a supported language that isn't the operator's -- that's the visitor,
        # whichever language it turns out to be, not necessarily whatever visitor_lang was
        # previously. Update it so the app's own "visitor language" display follows reality,
        # and so a translated *reply* back to them (see below) goes out in the right language.
        actual_src, actual_tgt = detected_short, operator_lang
        visitor_lang = detected_short
    else:
        # Sarvam didn't return a usable/supported language field at all (misdetection,
        # background noise -- check the "[STT] Full response" log if this fires a lot).
        # Fall back to the last known direction rather than guessing.
        if detected_short is not None:
            logger.warning(
                "Detected language '%s' is not a supported language; falling back to %s->%s",
                detected_short, operator_lang, visitor_lang,
            )
        actual_src, actual_tgt = operator_lang, visitor_lang

    await websocket.send_json({"type": "transcript", "text": transcript, "final": True, "lang": actual_src})

    faq_match = faq_state.match_faq(transcript, actual_src)
    if faq_match is not None:
        faq_entry, match_type = faq_match
        answer_text, answer_lang = faq_state.resolve_faq_answer(faq_entry, actual_src)
        # Emergency-category matches still get the pre-approved safe instruction spoken
        # immediately (never left unanswered), but are flagged for the dashboard/operator
        # queue -- loose matching must never be the only thing standing between a pilgrim
        # and help for a medical/missing-person/police situation. A fuzzy-matched emergency
        # is exactly the case worth watching most closely in the analytics/escalation queue.
        is_emergency = faq_entry["category"] == "emergency"
        logger.info(
            "FAQ matched (%s): id=%s lang=%s category=%s -> answering instantly",
            match_type, faq_entry["id"], answer_lang, faq_entry["category"],
        )
        await websocket.send_json({"type": "translation", "text": answer_text, "lang": answer_lang})
        try:
            pcm_audio = await get_faq_audio(faq_entry, answer_lang, answer_text)
        except SarvamAPIError as exc:
            logger.error("FAQ TTS failed for %s: %s", faq_entry["id"], exc)
            await websocket.send_json({"type": "error", "message": f"Speech synthesis failed: {exc}"})
            await db.log_analytics(
                EVENT_PACK, counter_id, actual_src, faq_entry["id"], "faq", False, False,
                stt_ms, int((time.perf_counter() - pipeline_start) * 1000),
                escalated=is_emergency, audio_duration_ms=audio_duration_ms, stt_cost_inr=stt_cost_val,
            )
            return visitor_lang
        chunk_count = 0
        for offset in range(0, len(pcm_audio), TTS_CHUNK_SIZE):
            chunk = pcm_audio[offset : offset + TTS_CHUNK_SIZE]
            await websocket.send_bytes(bytes([FLAG_TTS_AUDIO]) + chunk)
            chunk_count += 1
        logger.info("FAQ pipeline complete: sent %d audio chunks (%d bytes)", chunk_count, len(pcm_audio))
        # Translate and TTS were both skipped -- this is exactly the cost the FAQ cache saves,
        # only STT (unavoidable -- we still have to hear what was said) is a real charge here.
        await db.log_analytics(
            EVENT_PACK, counter_id, actual_src, faq_entry["id"], "faq", False, False,
            stt_ms, int((time.perf_counter() - pipeline_start) * 1000), escalated=is_emergency,
            audio_duration_ms=audio_duration_ms, stt_cost_inr=stt_cost_val,
        )
        return visitor_lang

    try:
        translated_text = await sarvam_translate(transcript, actual_src, actual_tgt)
    except SarvamAPIError as exc:
        logger.error("Translation failed: %s", exc)
        await websocket.send_json({"type": "error", "message": f"Translation failed: {exc}"})
        await db.log_analytics(
            EVENT_PACK, counter_id, actual_src, None, "none", False, False,
            stt_ms, int((time.perf_counter() - pipeline_start) * 1000),
            audio_duration_ms=audio_duration_ms, stt_cost_inr=stt_cost_val,
        )
        return visitor_lang

    if not translated_text.strip():
        logger.warning("NMT returned empty translation; aborting pipeline")
        await websocket.send_json({"type": "error", "message": "Translation returned empty result"})
        # Translate did succeed (just returned empty) -- that call is still billable.
        await db.log_analytics(
            EVENT_PACK, counter_id, actual_src, None, "none", True, False,
            stt_ms, int((time.perf_counter() - pipeline_start) * 1000),
            audio_duration_ms=audio_duration_ms, stt_cost_inr=stt_cost_val,
            translate_cost_inr=pricing.translate_cost(len(transcript)),
        )
        return visitor_lang

    unknown_question_note = (
        "No FAQ match -- fell through to live translation. If this question recurs, it's a "
        "candidate for a new approved FAQ entry (see the 'frequently unanswered' report)."
    )
    logger.info("%s | transcript=%r", unknown_question_note, transcript)

    await websocket.send_json({"type": "translation", "text": translated_text, "lang": actual_tgt})

    # Latency: a single sarvam_tts() call over the whole translated_text blocks until every
    # sentence is synthesized before the client hears anything. Splitting into sentences and
    # firing all of them at Sarvam concurrently (asyncio.create_task, not sequential awaits)
    # lets synthesis of sentence 2+ happen in the background while sentence 1's audio is
    # already being sent -- "time to first audio" drops to roughly one sentence's TTS latency
    # instead of the whole response's. For the common single-sentence reply this is a no-op:
    # one sentence in, one TTS call, identical to before.
    sentences = split_sentences(translated_text)
    tts_tasks = [asyncio.create_task(sarvam_tts(sentence, actual_tgt)) for sentence in sentences]

    chunk_count = 0
    total_bytes = 0
    synthesized_chars = 0  # only sentences that actually succeeded are billable
    try:
        for sentence, task in zip(sentences, tts_tasks):
            try:
                pcm_audio = await task
            except SarvamAPIError as exc:
                logger.error("TTS failed for sentence %r: %s", sentence, exc)
                await websocket.send_json({"type": "error", "message": f"Speech synthesis failed: {exc}"})
                await db.log_analytics(
                    EVENT_PACK, counter_id, actual_src, None, "none", True, synthesized_chars > 0,
                    stt_ms, int((time.perf_counter() - pipeline_start) * 1000),
                    audio_duration_ms=audio_duration_ms, stt_cost_inr=stt_cost_val,
                    translate_cost_inr=pricing.translate_cost(len(transcript)),
                    tts_cost_inr=pricing.tts_cost(synthesized_chars),
                )
                return visitor_lang

            pcm_audio = apply_edge_fade(pcm_audio)
            synthesized_chars += len(sentence)

            for offset in range(0, len(pcm_audio), TTS_CHUNK_SIZE):
                chunk = pcm_audio[offset : offset + TTS_CHUNK_SIZE]
                await websocket.send_bytes(bytes([FLAG_TTS_AUDIO]) + chunk)
                chunk_count += 1
            total_bytes += len(pcm_audio)
    finally:
        for task in tts_tasks:
            if not task.done():
                task.cancel()

    logger.info("Pipeline complete: sent %d audio chunks (%d bytes, %d sentence(s))", chunk_count, total_bytes, len(sentences))
    await db.log_analytics(
        EVENT_PACK, counter_id, actual_src, None, "none", True, True,
        stt_ms, int((time.perf_counter() - pipeline_start) * 1000),
        audio_duration_ms=audio_duration_ms, stt_cost_inr=stt_cost_val,
        translate_cost_inr=pricing.translate_cost(len(transcript)),
        tts_cost_inr=pricing.tts_cost(synthesized_chars),
    )
    return visitor_lang


@app.on_event("shutdown")
async def shutdown_event() -> None:
    await http_client.aclose()
    await db.close_pool()
