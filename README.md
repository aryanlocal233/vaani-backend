# Vaani Backend

Real-time bidirectional voice-to-voice translation backend for Indian regional
languages, built on FastAPI WebSockets and the Bhashini ULCA API (STT + NMT + TTS).

## Architecture

```
Android client
   │  binary WS frames (PCM16 mono 16kHz)
   ▼
api/websocket_handler.py   — protocol framing, session lifecycle, heartbeat watchdog
   │
   ├── audio/audio_buffer.py     — accumulates PCM per utterance
   ├── pipeline/simulmt.py       — incremental translation of partial transcripts
   └── pipeline/orchestrator.py  — full STT → NMT → TTS pipeline per utterance
           │
           ├── services/stt_service.py  (Bhashini primary, Azure fallback)
           ├── services/nmt_service.py  (Bhashini primary, Google fallback)
           └── services/tts_service.py  (Bhashini primary, Azure fallback)
                   │
                   └── services/bhashini_client.py — Bhashini ULCA HTTP client

session/session_manager.py — Redis-backed session state (1hr TTL)
```

## Binary WebSocket Protocol

Endpoint: `ws://<host>/ws/translate/{src_lang}/{tgt_lang}`

**Client → Server** (binary frames):

| byte[0] | Meaning         | Payload                  |
|---------|-----------------|---------------------------|
| `0x00`  | Heartbeat       | none                       |
| `0x01`  | Audio chunk     | raw PCM16 mono 16kHz       |
| `0x02`  | End of utterance| final PCM chunk (may be empty) |

**Server → Client**:

- Binary: `0x03` + raw PCM bytes (TTS audio, streamed in 4096-byte chunks)
- Text JSON: `{"type": "connected", "session_id": "...", "src_lang": "...", "tgt_lang": "..."}`
- Text JSON: `{"type": "transcript", "text": "...", "final": bool, "lang": "hi"}`
- Text JSON: `{"type": "translation", "text": "...", "lang": "ta"}`
- Text JSON: `{"type": "error", "message": "..."}`

## Supported Languages

`hi` `ta` `te` `bn` `kn` `mr` `gu` `pa` `ml` `or` `as` `en`

## Local Development

```bash
python -m venv .venv
source .venv/bin/activate    # or .venv\Scripts\activate on Windows
pip install -r requirements-dev.txt

cp .env.example .env         # fill in BHASHINI_API_KEY / BHASHINI_USER_ID

# Redis must be running locally, e.g.:
docker run -p 6379:6379 redis:7-alpine

uvicorn main:app --reload
```

## Docker Compose

```bash
cp .env.example .env
docker compose up --build
```

## Tests

```bash
pytest tests/ -v
```

Tests mock `BhashiniClient` and use `fakeredis` — no live Bhashini credentials
or Redis instance required.

## Manual WebSocket Testing

Using [`websocat`](https://github.com/vi/websocat):

```bash
websocat --binary ws://localhost:8000/ws/translate/hi/en
```

Using `wscat`:

```bash
wscat -c ws://localhost:8000/ws/translate/hi/en
```

Sending a heartbeat frame with `websocat` (0x00 byte):

```bash
printf '\x00' | websocat --binary ws://localhost:8000/ws/translate/hi/en
```

## Health Checks

- `GET /health` — liveness (process up)
- `GET /ready` — readiness (Redis reachable)
