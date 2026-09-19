from __future__ import annotations

import pytest
from starlette.websockets import WebSocketDisconnect


def test_connect_valid_language_pair(test_client):
    with test_client.websocket_connect("/ws/translate/hi/en") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "connected"
        assert msg["src_lang"] == "hi"
        assert msg["tgt_lang"] == "en"
        assert "session_id" in msg


def test_connect_invalid_language_closes_4008(test_client):
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with test_client.websocket_connect("/ws/translate/xx/en") as ws:
            ws.receive_json()
    assert exc_info.value.code == 4008


def test_heartbeat_pong(test_client):
    with test_client.websocket_connect("/ws/translate/hi/en") as ws:
        ws.receive_json()  # connected

        ws.send_bytes(bytes([0x00]))
        pong = ws.receive_bytes()
        assert pong == bytes([0x00])


def test_audio_chunk_buffers_without_immediate_response(test_client):
    with test_client.websocket_connect("/ws/translate/hi/en") as ws:
        ws.receive_json()  # connected

        # Well under the 200ms SimulMT trigger threshold (6400 bytes @ 16kHz/16-bit).
        ws.send_bytes(bytes([0x01]) + b"\x00\x00" * 50)

        # Heartbeat next: if the server had queued anything from the audio chunk,
        # this would receive that instead of the pong.
        ws.send_bytes(bytes([0x00]))
        pong = ws.receive_bytes()
        assert pong == bytes([0x00])


def test_end_of_utterance_runs_full_pipeline(test_client, fake_bhashini):
    with test_client.websocket_connect("/ws/translate/hi/en") as ws:
        ws.receive_json()  # connected

        ws.send_bytes(bytes([0x01]) + b"\x00\x01" * 50)
        ws.send_bytes(bytes([0x02]) + b"\x00\x01" * 50)

        transcript_msg = ws.receive_json()
        assert transcript_msg["type"] == "transcript"
        assert transcript_msg["text"] == fake_bhashini.asr_text
        assert transcript_msg["final"] is True

        translation_msg = ws.receive_json()
        assert translation_msg["type"] == "translation"
        assert translation_msg["text"] == fake_bhashini.translated_text

        audio_msg = ws.receive_bytes()
        assert audio_msg[0] == 0x03
        assert len(audio_msg) > 1


def test_barge_in_disconnect_mid_utterance_does_not_crash(test_client):
    with test_client.websocket_connect("/ws/translate/hi/en") as ws:
        ws.receive_json()  # connected
        ws.send_bytes(bytes([0x01]) + b"\x00\x01" * 50)
        # Disconnect abruptly without sending end-of-utterance.

    # Server must still be healthy for a subsequent connection.
    with test_client.websocket_connect("/ws/translate/ta/hi") as ws2:
        msg = ws2.receive_json()
        assert msg["type"] == "connected"
