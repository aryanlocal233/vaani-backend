from __future__ import annotations

import base64

import fakeredis.aioredis
import pytest
from fastapi.testclient import TestClient

import main as main_module
from services.bhashini_client import BhashiniNmtResponse, BhashiniSttResponse, BhashiniTtsResponse


class FakeBhashiniClient:
    """Stand-in for BhashiniClient that returns deterministic fixtures instead of
    making real HTTP calls, so tests never hit the network.
    """

    def __init__(self, *args, **kwargs) -> None:
        self.asr_text = "namaste duniya"
        self.translated_text = "hello world"
        self.tts_audio = b"\x01\x02" * 100

    async def asr(self, pcm_data: bytes, source_language: str, sampling_rate: int = 16000):
        return BhashiniSttResponse(text=self.asr_text, raw={})

    async def translate(self, text: str, source_language: str, target_language: str):
        return BhashiniNmtResponse(translated_text=self.translated_text, raw={})

    async def tts(self, text: str, target_language: str, gender: str = "female", sampling_rate: int = 16000):
        return BhashiniTtsResponse(audio_pcm=self.tts_audio, raw={})

    async def aclose(self) -> None:
        pass


@pytest.fixture
def fake_redis():
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest.fixture
def fake_bhashini(monkeypatch):
    fake_client = FakeBhashiniClient()
    monkeypatch.setattr(main_module, "BhashiniClient", lambda settings: fake_client)
    return fake_client


@pytest.fixture
def test_client(fake_redis, fake_bhashini, monkeypatch):
    monkeypatch.setattr(main_module.redis, "from_url", lambda *a, **k: fake_redis)
    with TestClient(main_module.app) as client:
        yield client
