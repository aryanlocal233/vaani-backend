"""Common interface every STT/translate/TTS provider implements, so the registry (registry.py)
can rank, call, and fail over between them without knowing any provider-specific detail.

Language codes everywhere in this interface are Vaani's own short ISO codes ('hi', 'ta', 'en',
...) -- each provider adapter is responsible for converting to whatever format its own API wants.
"""
from __future__ import annotations

from dataclasses import dataclass


class ProviderError(Exception):
    """Raised by any provider adapter on a failed call -- the registry catches this specifically
    to decide whether to fail over to the next candidate."""


class ProviderNotConfigured(ProviderError):
    """Raised when a provider is selected but has no credentials -- distinct from a live API
    failure so the registry can skip it silently in auto mode, but still surface a clear error
    if an admin has force-selected it."""


@dataclass
class STTResult:
    text: str
    detected_language: str | None  # Vaani short code, or None if the provider can't report one
    provider: str
    cost_inr: float = 0.0  # actual cost for whichever provider handled this -- see registry.py


@dataclass
class TranslateResult:
    text: str
    provider: str
    cost_inr: float = 0.0


@dataclass
class TTSResult:
    pcm_bytes: bytes
    provider: str
    cost_inr: float = 0.0


class STTProvider:
    name: str = "base"
    # Whether this provider can transcribe without being told the language in advance --
    # required for Vaani's open-set visitor-language detection (see sarvam_server.py's
    # run_sarvam_pipeline). A provider that can't do this is only usable when a specific
    # language is already known, never for the "who is this and what language" first guess.
    supports_auto_detect: bool = False
    languages: frozenset[str] = frozenset()

    def is_configured(self) -> bool:
        raise NotImplementedError

    async def transcribe(self, pcm_data: bytes, language_hint: str | None) -> STTResult:
        raise NotImplementedError


class TranslateProvider:
    name: str = "base"
    languages: frozenset[str] = frozenset()

    def is_configured(self) -> bool:
        raise NotImplementedError

    async def translate(self, text: str, src_lang: str, tgt_lang: str) -> TranslateResult:
        raise NotImplementedError


class TTSProvider:
    name: str = "base"
    languages: frozenset[str] = frozenset()

    def is_configured(self) -> bool:
        raise NotImplementedError

    async def synthesize(self, text: str, lang: str) -> TTSResult:
        raise NotImplementedError
