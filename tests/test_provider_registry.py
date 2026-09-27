"""Unit tests for providers/registry.py: cost-based ranking, circuit breaker tripping/cooldown,
real-time in-request failover, and the forced-provider admin override. Uses fake in-memory
adapters (same approach as test_failover_manual.py's manual verification, but as real assertions)
so nothing here makes a network call or needs real provider credentials.

_health and _forced_provider are module-level and shared across calls (by design -- that's how
the circuit breaker remembers state between requests), so every test resets them first.
"""
from __future__ import annotations

import pytest

import providers.registry as registry
from providers.base import ProviderError, ProviderNotConfigured, STTResult


def setup_function():
    registry._health.clear()
    registry._forced_provider = {"stt": None, "translate": None, "tts": None}


class FakeSTT:
    """Configurable fake STTProvider: pass a list of exceptions/results to raise/return in order,
    or a single value to always use."""

    name = "fake"
    supports_auto_detect = True
    languages = frozenset({"hi", "en"})

    def __init__(self, outcomes=None, configured: bool = True):
        self._outcomes = list(outcomes) if outcomes is not None else [STTResult("hello", "en", "fake")]
        self._configured = configured
        self.call_count = 0

    def is_configured(self) -> bool:
        return self._configured

    async def transcribe(self, pcm_data: bytes, language_hint):
        self.call_count += 1
        outcome = self._outcomes[min(self.call_count - 1, len(self._outcomes) - 1)]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


# --- _effective_score / ranking ---

def test_effective_score_equals_raw_cost_with_no_history():
    registry.COST_TABLE[("stt", "unit_test_provider")] = 1.0
    assert registry._effective_score("stt", "unit_test_provider") == 1.0


def test_effective_score_penalizes_recent_failures():
    registry.COST_TABLE[("stt", "flaky")] = 1.0
    registry._record_outcome("stt", "flaky", success=False)
    registry._record_outcome("stt", "flaky", success=True)
    score = registry._effective_score("stt", "flaky")
    assert score > 1.0  # a cheaper-on-paper provider with recent failures scores worse


def test_cheaper_healthy_provider_ranks_first(monkeypatch):
    monkeypatch.setitem(registry.STT_PROVIDERS, "cheap", FakeSTT())
    monkeypatch.setitem(registry.STT_PROVIDERS, "pricey", FakeSTT())
    registry.COST_TABLE[("stt", "cheap")] = 0.1
    registry.COST_TABLE[("stt", "pricey")] = 5.0
    ranked = registry._rank_candidates("stt", {"cheap": registry.STT_PROVIDERS["cheap"],
                                                "pricey": registry.STT_PROVIDERS["pricey"]}, "en", False)
    assert ranked[0] == "cheap"


def test_unconfigured_provider_excluded_from_ranking():
    providers = {"cheap": FakeSTT(configured=False), "pricey": FakeSTT()}
    registry.COST_TABLE[("stt", "cheap")] = 0.1
    registry.COST_TABLE[("stt", "pricey")] = 5.0
    ranked = registry._rank_candidates("stt", providers, "en", False)
    assert ranked == ["pricey"]


def test_provider_without_needed_language_excluded():
    fake = FakeSTT()
    fake.languages = frozenset({"ta"})  # doesn't support "en"
    ranked = registry._rank_candidates("stt", {"fake": fake}, "en", False)
    assert ranked == []


def test_auto_detect_required_excludes_non_supporting_provider():
    fake = FakeSTT()
    fake.supports_auto_detect = False
    ranked = registry._rank_candidates("stt", {"fake": fake}, None, True)
    assert ranked == []


# --- circuit breaker ---

def test_circuit_trips_after_consecutive_failures():
    for _ in range(registry.CIRCUIT_TRIP_CONSECUTIVE):
        registry._record_outcome("stt", "flaky", success=False)
    assert registry._is_circuit_open("stt", "flaky") is True


def test_circuit_stays_closed_below_consecutive_threshold():
    for _ in range(registry.CIRCUIT_TRIP_CONSECUTIVE - 1):
        registry._record_outcome("stt", "flaky", success=False)
    assert registry._is_circuit_open("stt", "flaky") is False


def test_single_success_resets_circuit():
    for _ in range(registry.CIRCUIT_TRIP_CONSECUTIVE):
        registry._record_outcome("stt", "flaky", success=False)
    assert registry._is_circuit_open("stt", "flaky") is True
    registry._record_outcome("stt", "flaky", success=True)
    assert registry._is_circuit_open("stt", "flaky") is False


def test_open_circuit_provider_ranked_last_not_excluded():
    # All candidates circuit-open -> still return the least-recently-failed one, rather than an
    # empty list (something is better than an outright refusal -- see module docstring).
    for _ in range(registry.CIRCUIT_TRIP_CONSECUTIVE):
        registry._record_outcome("stt", "only_option", success=False)
    fake = FakeSTT()
    ranked = registry._rank_candidates("stt", {"only_option": fake}, "en", False)
    assert ranked == ["only_option"]


# --- forced provider override ---

def test_forced_provider_bypasses_ranking_even_if_worse():
    registry._forced_provider["stt"] = "pricey"
    registry.COST_TABLE[("stt", "cheap")] = 0.1
    registry.COST_TABLE[("stt", "pricey")] = 5.0
    ranked = registry._rank_candidates("stt", {"cheap": FakeSTT(), "pricey": FakeSTT()}, "en", False)
    assert ranked == ["pricey"]


def test_forced_provider_bypasses_open_circuit():
    registry._forced_provider["stt"] = "flaky"
    for _ in range(registry.CIRCUIT_TRIP_CONSECUTIVE):
        registry._record_outcome("stt", "flaky", success=False)
    ranked = registry._rank_candidates("stt", {"flaky": FakeSTT()}, "en", False)
    assert ranked == ["flaky"]


# --- real-time in-request failover (transcribe()) ---

@pytest.mark.asyncio
async def test_transcribe_fails_over_to_next_candidate_on_error(monkeypatch):
    # transcribe() reads the module-global STT_PROVIDERS dict directly, unlike _rank_candidates
    # in the tests above (which take a dict as an argument) -- so this must *replace* that global
    # entirely, not add to it. Adding would leave the real sarvam/bhashini/azure adapters in the
    # candidate pool too, and if any is actually configured (a real API key in .env), ranking
    # could pick it and make a real, paid network call from a unit test.
    failing = FakeSTT(outcomes=[ProviderError("boom")])
    healthy = FakeSTT(outcomes=[STTResult("hello", "en", "healthy")])
    monkeypatch.setattr(registry, "STT_PROVIDERS", {"failing": failing, "healthy": healthy})
    registry.COST_TABLE[("stt", "failing")] = 0.1  # ranked first for being "cheaper"
    registry.COST_TABLE[("stt", "healthy")] = 5.0

    result = await registry.transcribe(b"\x00" * 3200, "en")

    assert result.provider == "healthy"
    assert failing.call_count == 1
    assert healthy.call_count == 1


@pytest.mark.asyncio
async def test_transcribe_raises_when_all_candidates_fail(monkeypatch):
    a = FakeSTT(outcomes=[ProviderError("a failed")])
    b = FakeSTT(outcomes=[ProviderError("b failed")])
    monkeypatch.setattr(registry, "STT_PROVIDERS", {"a": a, "b": b})
    registry.COST_TABLE[("stt", "a")] = 0.1
    registry.COST_TABLE[("stt", "b")] = 0.2

    with pytest.raises(ProviderError):
        await registry.transcribe(b"\x00" * 3200, "en")


@pytest.mark.asyncio
async def test_transcribe_raises_when_no_candidates_configured(monkeypatch):
    monkeypatch.setattr(registry, "STT_PROVIDERS", {"unconfigured": FakeSTT(configured=False)})
    with pytest.raises(ProviderError):
        await registry.transcribe(b"\x00" * 3200, "en")
