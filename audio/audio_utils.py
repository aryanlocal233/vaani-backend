"""PCM audio validation and sample-rate helpers.

All audio in this system is assumed to be raw PCM, 16-bit signed
little-endian, mono, at :data:`DEFAULT_SAMPLE_RATE` unless otherwise noted.
"""
from __future__ import annotations

BYTES_PER_SAMPLE = 2  # 16-bit PCM
CHANNELS = 1
DEFAULT_SAMPLE_RATE = 16000


def is_valid_pcm(data: bytes) -> bool:
    """A valid PCM16 buffer has an even number of bytes (whole samples)."""
    return len(data) > 0 and len(data) % BYTES_PER_SAMPLE == 0


def pcm_duration_ms(data: bytes, sample_rate: int = DEFAULT_SAMPLE_RATE) -> float:
    """Duration in milliseconds of a raw PCM16 mono buffer."""
    if not data:
        return 0.0
    sample_count = len(data) // BYTES_PER_SAMPLE
    return (sample_count / sample_rate) * 1000.0


def bytes_for_duration_ms(duration_ms: float, sample_rate: int = DEFAULT_SAMPLE_RATE) -> int:
    """Number of PCM16 bytes needed to represent `duration_ms` of audio."""
    sample_count = int((duration_ms / 1000.0) * sample_rate)
    return sample_count * BYTES_PER_SAMPLE


def chunk_bytes(data: bytes, chunk_size: int):
    """Yield successive `chunk_size`-byte slices of `data`."""
    for i in range(0, len(data), chunk_size):
        yield data[i : i + chunk_size]
