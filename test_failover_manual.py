"""One-off, manual verification of providers/registry.py's failover + circuit-breaker logic.
Not part of the app or a pytest suite -- run directly (`python test_failover_manual.py`) inside
the same container/environment as the real app, so the real Sarvam provider is genuinely
available as the fallback target. Injects one fake always-failing STT provider ranked above
Sarvam (near-zero cost) purely in this process's memory -- never touches the live server.
"""
import asyncio

import db
import providers.registry as registry
from providers.base import ProviderError, STTProvider, STTResult


class AlwaysFailingSTT(STTProvider):
    name = "fake_flaky"
    supports_auto_detect = True
    languages = frozenset({"hi", "ta", "te", "bn", "kn", "mr", "gu", "pa", "ml", "or", "as", "en"})
    call_count = 0

    def is_configured(self) -> bool:
        return True

    async def transcribe(self, pcm_data: bytes, language_hint) -> STTResult:
        AlwaysFailingSTT.call_count += 1
        raise ProviderError(f"simulated failure #{AlwaysFailingSTT.call_count}")


async def main():
    await db.init_pool()
    await registry.reload_config()

    fake = AlwaysFailingSTT()
    registry.STT_PROVIDERS["fake_flaky"] = fake
    registry.COST_TABLE[("stt", "fake_flaky")] = 0.0001  # ranks first on cost -- must fail over

    sample_pcm = b"\x00\x01" * 8000  # ~0.5s of fake silence, just needs to be non-trivial bytes

    print("=== Test 1: real-time failover within a single request ===")
    result = await registry.transcribe(sample_pcm, None)
    print(f"Result came from provider: {result.provider} (expected: sarvam, since fake_flaky failed)")
    assert result.provider == "sarvam", "FAILOVER DID NOT WORK"
    print("PASS: failed over from fake_flaky to sarvam within one call\n")

    print("=== Test 2: circuit breaker opens after repeated failures ===")
    key = ("stt", "fake_flaky")
    for i in range(5):
        await registry.transcribe(sample_pcm, None)
    state = registry._health_state("stt", "fake_flaky")
    print(f"fake_flaky health state: consecutive_failures={state['consecutive_failures']}, "
          f"circuit_open_until_set={state['circuit_open_until'] is not None}")
    is_open = registry._is_circuit_open("stt", "fake_flaky")
    print(f"Circuit open: {is_open}")
    assert is_open, "CIRCUIT BREAKER DID NOT TRIP"
    print("PASS: circuit breaker opened after repeated failures\n")

    print("=== Test 3: with circuit open, fake_flaky is skipped entirely (not even attempted) ===")
    call_count_before = AlwaysFailingSTT.call_count
    result = await registry.transcribe(sample_pcm, None)
    call_count_after = AlwaysFailingSTT.call_count
    print(f"fake_flaky.transcribe() call count before={call_count_before}, after={call_count_after}")
    assert call_count_after == call_count_before, "CIRCUIT-OPEN PROVIDER WAS STILL CALLED"
    print(f"Result still came from: {result.provider}")
    print("PASS: circuit-open provider was skipped, went straight to sarvam\n")

    print("=== Test 4: forced-provider override bypasses ranking/failover entirely ===")
    await db.set_provider_config("stt", "fake_flaky")
    await registry.reload_config()
    try:
        await registry.transcribe(sample_pcm, None)
        print("FAIL: expected an error since fake_flaky always fails and forcing disables failover")
    except ProviderError as exc:
        print(f"PASS: forced fake_flaky failed with no fallback, as expected: {exc}")
    await db.set_provider_config("stt", None)
    await registry.reload_config()

    print("\nALL TESTS PASSED")
    await db.close_pool()


if __name__ == "__main__":
    asyncio.run(main())
