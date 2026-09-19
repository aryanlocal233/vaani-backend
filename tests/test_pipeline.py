from __future__ import annotations

import time

import pytest

from pipeline.orchestrator import Orchestrator
from pipeline.simulmt import SimulMTProcessor
from services.nmt_service import NMTResult
from services.stt_service import STTResult
from session.session_manager import SessionState


class FakeWebSocket:
    def __init__(self) -> None:
        self.json_messages: list[dict] = []
        self.binary_messages: list[bytes] = []

    async def send_json(self, payload: dict) -> None:
        self.json_messages.append(payload)

    async def send_bytes(self, data: bytes) -> None:
        self.binary_messages.append(data)


class FakeSTTService:
    def __init__(self, text: str = "hello there") -> None:
        self.text = text

    async def transcribe(self, pcm_data: bytes, language: str) -> STTResult:
        return STTResult(text=self.text, language=language, confidence=1.0, duration_ms=1.0)


class FailingSTTService:
    async def transcribe(self, pcm_data: bytes, language: str) -> STTResult:
        raise RuntimeError("stt boom")


class FakeNMTService:
    def __init__(self, translated: str = "namaste") -> None:
        self.translated = translated

    async def translate(self, text: str, src_lang: str, tgt_lang: str) -> NMTResult:
        if not text.strip():
            return NMTResult(translated_text="", src_lang=src_lang, tgt_lang=tgt_lang, duration_ms=0.0)
        return NMTResult(
            translated_text=self.translated, src_lang=src_lang, tgt_lang=tgt_lang, duration_ms=1.0
        )


class FakeTTSService:
    def __init__(self, chunks: list[bytes] | None = None) -> None:
        self.chunks = chunks if chunks is not None else [b"chunk1", b"chunk2"]

    async def synthesize_stream(self, text: str, language: str):
        for chunk in self.chunks:
            yield chunk


def make_session() -> SessionState:
    now = time.time()
    return SessionState(
        session_id="test-session",
        src_lang="hi",
        tgt_lang="en",
        created_at=now,
        last_activity=now,
    )


@pytest.mark.asyncio
async def test_orchestrator_full_pipeline_sends_transcript_translation_and_audio():
    orchestrator = Orchestrator(FakeSTTService("namaste"), FakeNMTService("hello"), FakeTTSService())
    websocket = FakeWebSocket()
    session = make_session()

    await orchestrator.process_utterance(
        session=session, pcm_data=b"\x00\x01" * 100, src_lang="hi", tgt_lang="en", websocket=websocket
    )

    assert websocket.json_messages[0] == {
        "type": "transcript",
        "text": "namaste",
        "final": True,
        "lang": "hi",
    }
    assert websocket.json_messages[1] == {"type": "translation", "text": "hello", "lang": "en"}
    assert websocket.binary_messages == [b"\x03chunk1", b"\x03chunk2"]


@pytest.mark.asyncio
async def test_orchestrator_empty_transcript_skips_nmt_and_tts():
    nmt = FakeNMTService("should-not-be-sent")
    orchestrator = Orchestrator(FakeSTTService(""), nmt, FakeTTSService())
    websocket = FakeWebSocket()
    session = make_session()

    await orchestrator.process_utterance(
        session=session, pcm_data=b"\x00\x01", src_lang="hi", tgt_lang="en", websocket=websocket
    )

    assert len(websocket.json_messages) == 1
    assert websocket.json_messages[0]["type"] == "transcript"
    assert websocket.binary_messages == []


@pytest.mark.asyncio
async def test_orchestrator_stt_failure_sends_error_and_does_not_raise():
    orchestrator = Orchestrator(FailingSTTService(), FakeNMTService(), FakeTTSService())
    websocket = FakeWebSocket()
    session = make_session()

    await orchestrator.process_utterance(
        session=session, pcm_data=b"\x00\x01", src_lang="hi", tgt_lang="en", websocket=websocket
    )

    assert websocket.json_messages[0]["type"] == "error"
    assert "Speech recognition failed" in websocket.json_messages[0]["message"]


@pytest.mark.asyncio
async def test_simulmt_translates_on_clause_boundary():
    nmt = FakeNMTService("hello,")
    simulmt = SimulMTProcessor(nmt, "hi", "en")
    websocket = FakeWebSocket()

    await simulmt.on_partial_transcript("this is a longer clause,", websocket)

    assert len(websocket.json_messages) == 1
    assert websocket.json_messages[0]["type"] == "translation"
    assert websocket.json_messages[0]["text"] == "hello,"


@pytest.mark.asyncio
async def test_simulmt_no_translation_below_min_words():
    nmt = FakeNMTService("should not appear")
    simulmt = SimulMTProcessor(nmt, "hi", "en")
    websocket = FakeWebSocket()

    await simulmt.on_partial_transcript("hi,", websocket)

    assert websocket.json_messages == []


@pytest.mark.asyncio
async def test_simulmt_finalize_sends_authoritative_translation():
    nmt = FakeNMTService("final translation")
    simulmt = SimulMTProcessor(nmt, "hi", "en")
    websocket = FakeWebSocket()

    result = await simulmt.finalize("the complete final utterance text", websocket)

    assert result == "final translation"
    assert websocket.json_messages[-1] == {
        "type": "translation",
        "text": "final translation",
        "lang": "en",
    }
