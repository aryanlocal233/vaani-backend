from __future__ import annotations

import base64

import httpx
import pytest

from config import Settings
from services.bhashini_client import BhashiniAPIError, BhashiniClient


def make_settings() -> Settings:
    return Settings(
        BHASHINI_API_KEY="test-key",
        BHASHINI_USER_ID="test-user",
        BHASHINI_BASE_URL="https://dhruva-api.bhashini.gov.in",
    )


def make_client(handler) -> BhashiniClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport, base_url="https://dhruva-api.bhashini.gov.in")
    return BhashiniClient(make_settings(), client=http_client)


@pytest.mark.asyncio
async def test_asr_parses_response():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "test-key"
        assert request.headers["userID"] == "test-user"
        return httpx.Response(
            200,
            json={"pipelineResponse": [{"output": [{"source": "namaste"}]}]},
        )

    client = make_client(handler)
    result = await client.asr(b"\x00\x01", "hi")
    assert result.text == "namaste"
    await client.aclose()


@pytest.mark.asyncio
async def test_translate_parses_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"pipelineResponse": [{"output": [{"target": "hello"}]}]},
        )

    client = make_client(handler)
    result = await client.translate("namaste", "hi", "en")
    assert result.translated_text == "hello"
    await client.aclose()


@pytest.mark.asyncio
async def test_tts_decodes_base64_audio():
    raw_audio = b"\x01\x02\x03\x04"
    encoded = base64.b64encode(raw_audio).decode("ascii")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"pipelineResponse": [{"audio": [{"audioContent": encoded}]}]},
        )

    client = make_client(handler)
    result = await client.tts("hello", "hi")
    assert result.audio_pcm == raw_audio
    await client.aclose()


@pytest.mark.asyncio
async def test_retries_on_429_then_succeeds():
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        if call_count["n"] < 3:
            return httpx.Response(429, json={"error": "rate limited"})
        return httpx.Response(
            200,
            json={"pipelineResponse": [{"output": [{"source": "ok"}]}]},
        )

    client = make_client(handler)
    result = await client.asr(b"\x00\x01", "hi")
    assert result.text == "ok"
    assert call_count["n"] == 3
    await client.aclose()


@pytest.mark.asyncio
async def test_raises_on_persistent_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "server error"})

    client = make_client(handler)
    with pytest.raises(BhashiniAPIError):
        await client.asr(b"\x00\x01", "hi")
    await client.aclose()


@pytest.mark.asyncio
async def test_malformed_response_raises_bhashini_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})

    client = make_client(handler)
    with pytest.raises(BhashiniAPIError):
        await client.asr(b"\x00\x01", "hi")
    await client.aclose()
