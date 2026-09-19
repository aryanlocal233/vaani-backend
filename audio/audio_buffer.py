"""Per-session PCM accumulation buffer for a single utterance."""
from __future__ import annotations

import io

from audio.audio_utils import DEFAULT_SAMPLE_RATE, pcm_duration_ms


class AudioBuffer:
    """Accumulates raw PCM16 chunks for one in-progress utterance.

    Not thread-safe; intended to be owned by a single WebSocket session
    coroutine, which is how FastAPI/Starlette handles each connection.
    """

    def __init__(self, sample_rate: int = DEFAULT_SAMPLE_RATE) -> None:
        self._sample_rate = sample_rate
        self._stream = io.BytesIO()

    def add_chunk(self, pcm: bytes) -> None:
        if pcm:
            self._stream.write(pcm)

    def get_all(self) -> bytes:
        return self._stream.getvalue()

    def flush(self) -> bytes:
        data = self._stream.getvalue()
        self._stream = io.BytesIO()
        return data

    def duration_ms(self) -> float:
        return pcm_duration_ms(self._stream.getvalue(), self._sample_rate)

    def is_empty(self) -> bool:
        return self._stream.tell() == 0

    def __len__(self) -> int:
        return self._stream.tell()
