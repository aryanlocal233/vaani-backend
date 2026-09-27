"""Provider selection, real-time failover, circuit breaking, and usage logging for
STT/translate/TTS. This is the one place that decides "which provider handles this call" --
sarvam_server.py's pipeline calls transcribe()/translate()/synthesize() here instead of calling
any single provider directly.

============================== SELECTION LOGIC (read this first) ==============================

For each call, candidates are every provider that (a) is configured (has real credentials) and
(b) declares support for the language needed -- for STT specifically, a provider must also
support auto-detect if no specific language is known yet (only Sarvam does today).

If an admin has force-selected a provider for this capability (see /admin/providers), that
provider alone is used -- no ranking, no automatic failover to another provider if it fails,
since "force" is meant as a deliberate override for testing/control. Its failure surfaces as a
normal error.

Otherwise (auto mode), candidates are ranked by an effective cost score, cheapest first:

    effective_score = raw_cost_per_unit * (1 + 2*recent_failure_rate + 1*recent_quality_flag_rate)

Raw cost alone decides ranking among equally-reliable providers (this is "low cost" as the
primary axis, per the brief) -- but a provider that's been failing or producing flagged-quality
output recently gets pushed down the list even if it's nominally cheaper, because its *effective*
cost (accounting for retries, bad answers, lost trust) is higher than the number on its price
list. "recent" = last 20 calls to that provider for this capability, tracked in memory.

A provider whose recent failures tripped its circuit breaker (3 consecutive failures, or >50%
failure rate over its last 10 calls) is skipped entirely while its circuit is open, UNLESS every
candidate is currently circuit-open, in which case the least-recently-failed one is tried anyway
as a last resort (something is better than an outright refusal). The circuit's cooldown starts at
30s and doubles (capped at 5 minutes) each time a post-cooldown retry immediately fails again;
one success resets it back to healthy.

Within a single request, if the top-ranked candidate's call actually fails, the *next*-ranked
candidate is tried immediately (real-time failover, not just "better luck next request") -- up to
every remaining candidate before the call is reported as failed.

Every call (success or failure, whichever provider handled it) is logged to the provider_calls
table with cost, latency, and language, for the /admin/providers usage report.
=================================================================================================
"""
from __future__ import annotations

import logging
import time
from collections import deque

import db
from providers.base import ProviderError, ProviderNotConfigured, STTResult, TranslateResult, TTSResult
from providers.sarvam_provider import SarvamSTT, SarvamTranslate, SarvamTTS
from providers.bhashini_provider import BhashiniSTT, BhashiniTranslate, BhashiniTTS
from providers.azure_provider import AzureSTT, AzureTranslate, AzureTTS

logger = logging.getLogger("vaani.providers")

# Cost per unit: STT is ₹/minute of audio, translate/TTS are ₹/character. Bhashini's government
# mission pricing is free/negligible for this kind of public-good use -- treated as 0 for ranking,
# which correctly makes it win on cost the moment it's configured and healthy.
COST_TABLE = {
    ("stt", "sarvam"): 0.50, ("stt", "bhashini"): 0.0, ("stt", "azure"): 1.42,
    ("translate", "sarvam"): 0.005, ("translate", "bhashini"): 0.0, ("translate", "azure"): 0.00085,
    ("tts", "sarvam"): 0.003, ("tts", "bhashini"): 0.0, ("tts", "azure"): 0.00136,
}

STT_PROVIDERS = {"sarvam": SarvamSTT(), "bhashini": BhashiniSTT(), "azure": AzureSTT()}
TRANSLATE_PROVIDERS = {"sarvam": SarvamTranslate(), "bhashini": BhashiniTranslate(), "azure": AzureTranslate()}
TTS_PROVIDERS = {"sarvam": SarvamTTS(), "bhashini": BhashiniTTS(), "azure": AzureTTS()}

BASE_COOLDOWN_S = 30
MAX_COOLDOWN_S = 300
WINDOW_SIZE = 20
CIRCUIT_TRIP_CONSECUTIVE = 3
CIRCUIT_TRIP_FAILURE_RATE = 0.5
CIRCUIT_TRIP_MIN_SAMPLES = 10

_health: dict[tuple[str, str], dict] = {}
_forced_provider: dict[str, str | None] = {"stt": None, "translate": None, "tts": None}


def _health_state(capability: str, provider: str) -> dict:
    key = (capability, provider)
    if key not in _health:
        _health[key] = {"outcomes": deque(maxlen=WINDOW_SIZE), "consecutive_failures": 0,
                         "circuit_open_until": None, "cooldown_s": BASE_COOLDOWN_S,
                         "quality_flags": deque(maxlen=WINDOW_SIZE)}
    return _health[key]


def _record_outcome(capability: str, provider: str, success: bool, quality_flag: bool = False) -> None:
    state = _health_state(capability, provider)
    state["outcomes"].append(success)
    state["quality_flags"].append(quality_flag)
    if success:
        state["consecutive_failures"] = 0
        state["circuit_open_until"] = None
        state["cooldown_s"] = BASE_COOLDOWN_S
    else:
        state["consecutive_failures"] += 1
        outcomes = state["outcomes"]
        failure_rate = (len(outcomes) - sum(outcomes)) / len(outcomes) if outcomes else 0
        should_trip = (
            state["consecutive_failures"] >= CIRCUIT_TRIP_CONSECUTIVE
            or (len(outcomes) >= CIRCUIT_TRIP_MIN_SAMPLES and failure_rate > CIRCUIT_TRIP_FAILURE_RATE)
        )
        if should_trip:
            was_already_open = state["circuit_open_until"] is not None
            if was_already_open:
                state["cooldown_s"] = min(state["cooldown_s"] * 2, MAX_COOLDOWN_S)
            state["circuit_open_until"] = time.monotonic() + state["cooldown_s"]
            logger.warning(
                "Circuit breaker OPEN for %s/%s (cooldown=%ss, consecutive_failures=%d, recent_failure_rate=%.0f%%)",
                capability, provider, state["cooldown_s"], state["consecutive_failures"], failure_rate * 100,
            )


def _is_circuit_open(capability: str, provider: str) -> bool:
    state = _health_state(capability, provider)
    if state["circuit_open_until"] is None:
        return False
    if time.monotonic() >= state["circuit_open_until"]:
        return False  # cooldown elapsed -- allow a half-open trial call through
    return True


def _effective_score(capability: str, provider: str) -> float:
    state = _health_state(capability, provider)
    outcomes = state["outcomes"]
    failure_rate = (len(outcomes) - sum(outcomes)) / len(outcomes) if outcomes else 0.0
    quality_flags = state["quality_flags"]
    quality_flag_rate = sum(quality_flags) / len(quality_flags) if quality_flags else 0.0
    raw_cost = COST_TABLE.get((capability, provider), 999.0)
    return raw_cost * (1 + 2 * failure_rate + 1 * quality_flag_rate)


async def reload_config() -> None:
    """Loads admin-forced provider overrides from Postgres into memory. Call at startup and
    whenever an admin changes the setting -- mirrors faq_state.reload()'s pattern."""
    rows = await db.get_provider_config()
    for capability in ("stt", "translate", "tts"):
        _forced_provider[capability] = None
    for row in rows:
        _forced_provider[row["capability"]] = row["forced_provider"]
    logger.info("Provider config loaded: %s", _forced_provider)


def _rank_candidates(capability: str, providers: dict, language: str, needs_auto_detect: bool) -> list[str]:
    forced = _forced_provider.get(capability)
    if forced is not None:
        return [forced]

    eligible = []
    for name, adapter in providers.items():
        if not adapter.is_configured():
            continue
        if language not in adapter.languages and language is not None:
            continue
        if needs_auto_detect and not adapter.supports_auto_detect:
            continue
        eligible.append(name)

    if not eligible:
        return []

    open_ones = [p for p in eligible if _is_circuit_open(capability, p)]
    closed_ones = [p for p in eligible if not _is_circuit_open(capability, p)]
    ranked_closed = sorted(closed_ones, key=lambda p: _effective_score(capability, p))

    if ranked_closed:
        return ranked_closed
    # Every candidate is circuit-open -- try the one whose cooldown ends soonest rather than
    # refuse outright.
    ranked_open = sorted(open_ones, key=lambda p: _health_state(capability, p)["circuit_open_until"])
    return ranked_open


async def _log_call(capability: str, provider: str, language: str | None, success: bool,
                     latency_ms: int, cost_inr: float, error: str | None, quality_flag: bool) -> None:
    try:
        await db.log_provider_call(capability, provider, language, success, latency_ms, cost_inr, error, quality_flag)
    except Exception:
        logger.exception("Failed to log provider call (non-fatal)")


async def transcribe(pcm_data: bytes, language_hint: str | None) -> STTResult:
    needs_auto_detect = not language_hint or language_hint == "unknown"
    candidates = _rank_candidates("stt", STT_PROVIDERS, language_hint, needs_auto_detect)
    if not candidates:
        raise ProviderError("No configured STT provider available for this request")

    last_error: Exception | None = None
    for provider_name in candidates:
        adapter = STT_PROVIDERS[provider_name]
        start = time.perf_counter()
        try:
            result = await adapter.transcribe(pcm_data, language_hint)
            latency_ms = int((time.perf_counter() - start) * 1000)
            audio_minutes = len(pcm_data) / 32 / 60000
            result.cost_inr = COST_TABLE.get(("stt", provider_name), 0.0) * audio_minutes
            quality_flag = len(result.text.strip()) < 2 and len(pcm_data) > 32 * 2000  # long audio, near-empty transcript
            _record_outcome("stt", provider_name, True, quality_flag)
            await _log_call("stt", provider_name, result.detected_language, True, latency_ms, result.cost_inr, None, quality_flag)
            return result
        except ProviderNotConfigured as exc:
            # Only matters if this was a forced single-candidate selection -- in auto mode,
            # is_configured() already filtered these out before ranking, so this path is only
            # reached when an admin force-selected a provider that has no credentials.
            last_error = ProviderError(f"Forced provider '{provider_name}' is not configured: {exc}")
            continue
        except ProviderError as exc:
            latency_ms = int((time.perf_counter() - start) * 1000)
            _record_outcome("stt", provider_name, False)
            await _log_call("stt", provider_name, language_hint, False, latency_ms, 0.0, str(exc), False)
            last_error = exc
            logger.warning("STT provider %s failed, trying next candidate: %s", provider_name, exc)

    raise last_error or ProviderError("All STT providers failed")


async def translate(text: str, src_lang: str, tgt_lang: str) -> TranslateResult:
    candidates = _rank_candidates("translate", TRANSLATE_PROVIDERS, src_lang, False)
    candidates = [p for p in candidates if tgt_lang in TRANSLATE_PROVIDERS[p].languages] or candidates
    if not candidates:
        raise ProviderError("No configured translation provider available for this language pair")

    last_error: Exception | None = None
    for provider_name in candidates:
        adapter = TRANSLATE_PROVIDERS[provider_name]
        start = time.perf_counter()
        try:
            result = await adapter.translate(text, src_lang, tgt_lang)
            latency_ms = int((time.perf_counter() - start) * 1000)
            result.cost_inr = COST_TABLE.get(("translate", provider_name), 0.0) * len(text)
            ratio = len(result.text) / max(len(text), 1)
            quality_flag = ratio < 0.2 or ratio > 4.0
            _record_outcome("translate", provider_name, True, quality_flag)
            await _log_call("translate", provider_name, f"{src_lang}->{tgt_lang}", True, latency_ms, result.cost_inr, None, quality_flag)
            return result
        except ProviderNotConfigured as exc:
            last_error = ProviderError(f"Forced provider '{provider_name}' is not configured: {exc}")
            continue
        except ProviderError as exc:
            latency_ms = int((time.perf_counter() - start) * 1000)
            _record_outcome("translate", provider_name, False)
            await _log_call("translate", provider_name, f"{src_lang}->{tgt_lang}", False, latency_ms, 0.0, str(exc), False)
            last_error = exc
            logger.warning("Translate provider %s failed, trying next candidate: %s", provider_name, exc)

    raise last_error or ProviderError("All translation providers failed")


async def synthesize(text: str, lang: str) -> TTSResult:
    candidates = _rank_candidates("tts", TTS_PROVIDERS, lang, False)
    if not candidates:
        raise ProviderError("No configured TTS provider available for this language")

    last_error: Exception | None = None
    for provider_name in candidates:
        adapter = TTS_PROVIDERS[provider_name]
        start = time.perf_counter()
        try:
            result = await adapter.synthesize(text, lang)
            latency_ms = int((time.perf_counter() - start) * 1000)
            result.cost_inr = COST_TABLE.get(("tts", provider_name), 0.0) * len(text)
            quality_flag = len(result.pcm_bytes) < 32 * 100  # under 100ms of audio for real text is suspicious
            _record_outcome("tts", provider_name, True, quality_flag)
            await _log_call("tts", provider_name, lang, True, latency_ms, result.cost_inr, None, quality_flag)
            return result
        except ProviderNotConfigured as exc:
            last_error = ProviderError(f"Forced provider '{provider_name}' is not configured: {exc}")
            continue
        except ProviderError as exc:
            latency_ms = int((time.perf_counter() - start) * 1000)
            _record_outcome("tts", provider_name, False)
            await _log_call("tts", provider_name, lang, False, latency_ms, 0.0, str(exc), False)
            last_error = exc
            logger.warning("TTS provider %s failed, trying next candidate: %s", provider_name, exc)

    raise last_error or ProviderError("All TTS providers failed")
