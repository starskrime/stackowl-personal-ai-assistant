"""D01.2 — the Anthropic provider half: capability gate, chokepoint, reader.

The replay fixtures here are built from the Anthropic API DOCUMENTATION, not
captured from the real API. This box runs no Anthropic backend, so a passing test
proves we send the shape the documentation describes and read the fields it names
— it does NOT prove Anthropic honours the markers. See designs/D01.2.md,
"WHAT CANNOT BE VERIFIED HERE".
"""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.config.provider import ProviderConfig
from stackowl.config.test_mode import TestModeGuard
from stackowl.providers.anthropic_provider import AnthropicProvider
from stackowl.providers.base import Message
from stackowl.providers.mock_provider import MockProvider

pytestmark = pytest.mark.asyncio


def _cfg(**overrides: object) -> ProviderConfig:
    base: dict[str, object] = {
        "name": "anthropic-main",
        "protocol": "anthropic",
        "default_model": "claude-opus-5",
        "tiers": ("powerful",),
    }
    base.update(overrides)
    return ProviderConfig(**base)  # type: ignore[arg-type]


def _provider(**overrides: object) -> AnthropicProvider:
    return AnthropicProvider(_cfg(**overrides), api_key="test-key-not-used")


def _big(tokens: int) -> str:
    return "x" * (tokens * 5 + 8)


def _tools() -> list[dict[str, Any]]:
    return [{"name": "shell", "description": _big(700), "input_schema": {"type": "object"}}]


def _markers(obj: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key == "cache_control":
                found.append(value)
            else:
                found.extend(_markers(value))
    elif isinstance(obj, list):
        for item in obj:
            found.extend(_markers(item))
    return found


class _CountTokens:
    """Scripted ``messages.count_tokens`` — records every call it is asked for."""

    def __init__(self, input_tokens: int = 4000) -> None:
        self.calls: list[dict[str, Any]] = []
        self._input_tokens = input_tokens

    async def __call__(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return type("Count", (), {"input_tokens": self._input_tokens})()


class _RecordingTracker:
    """Captures the kwargs every ``_record_cost`` hands to the cost tracker."""

    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    async def record(self, **kwargs: Any) -> None:
        self.records.append(kwargs)


# ---------------------------------------------------------------------------
# I4 — the capability gate is what makes shipping ON safe
# ---------------------------------------------------------------------------

async def test_the_base_provider_declares_no_cache_breakpoint_support() -> None:
    """Default False on the ABC, matching supports_vision/supports_document.

    This is invariant I4: every provider that does not opt in sends a request
    byte-identical to today's, which is what lets D01.2 ship ON by default with no
    enable flag on a deployment that cannot use it.
    """
    assert MockProvider(_cfg(protocol="openai")).supports_cache_breakpoints is False


async def test_the_anthropic_provider_declares_cache_breakpoint_support() -> None:
    assert _provider().supports_cache_breakpoints is True


async def test_a_provider_without_the_capability_is_never_marked() -> None:
    """Dispatch is on the DECLARED CAPABILITY, never on a provider's name."""
    provider = _provider()
    kwargs = await provider._request_kwargs(
        model="claude-opus-5",
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=100,
        tools=_tools(),
        system=_big(800),
        capable=False,
    )
    assert _markers(kwargs) == []
    assert kwargs["system"] == _big(800)  # still a plain string — byte-identical


# ---------------------------------------------------------------------------
# The chokepoint
# ---------------------------------------------------------------------------

async def test_the_chokepoint_marks_a_capable_request() -> None:
    provider = _provider()
    provider._client.messages.count_tokens = _CountTokens(4000)  # type: ignore[assignment]
    kwargs = await provider._request_kwargs(
        model="claude-opus-5",
        messages=[
            {"role": "user", "content": "one"},
            {"role": "assistant", "content": "two"},
            {"role": "user", "content": "three"},
        ],
        max_tokens=100,
        tools=_tools(),
        system=_big(800),
    )
    assert len(_markers(kwargs)) == 4
    assert kwargs["tools"][-1]["cache_control"] == {"type": "ephemeral"}


async def test_the_configured_ttl_reaches_every_marker() -> None:
    provider = _provider(cache_ttl="1h")
    provider._client.messages.count_tokens = _CountTokens(4000)  # type: ignore[assignment]
    kwargs = await provider._request_kwargs(
        model="claude-opus-5",
        messages=[{"role": "user", "content": "one"}, {"role": "user", "content": "two"}],
        max_tokens=100,
        tools=_tools(),
        system=_big(800),
    )
    markers = _markers(kwargs)
    assert markers, "expected markers to be placed"
    for marker in markers:
        assert marker == {"type": "ephemeral", "ttl": "1h"}


# ---------------------------------------------------------------------------
# I3 — a marking failure never fails the turn
# ---------------------------------------------------------------------------

async def test_a_marking_failure_sends_the_request_unmarked(monkeypatch: Any) -> None:
    """I3 — caching is an optimisation, never a dependency.

    The D01.2 mirror of D01.1's I2: if the marker layer raises, the turn still
    goes out, just at full price.
    """
    def _explode(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("marker layer is broken")

    monkeypatch.setattr(
        "stackowl.providers.anthropic_provider.apply_cache_breakpoints", _explode
    )
    provider = _provider()
    provider._client.messages.count_tokens = _CountTokens(4000)  # type: ignore[assignment]

    kwargs = await provider._request_kwargs(
        model="claude-opus-5",
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=100,
        tools=_tools(),
        system=_big(800),
    )
    assert _markers(kwargs) == []
    assert kwargs["system"] == _big(800)
    assert kwargs["messages"] == [{"role": "user", "content": "hi"}]


async def test_a_marking_failure_is_logged_at_error(monkeypatch: Any, caplog: Any) -> None:
    """No hidden errors — a swallowed exception must still be visible."""
    def _explode(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("marker layer is broken")

    monkeypatch.setattr(
        "stackowl.providers.anthropic_provider.apply_cache_breakpoints", _explode
    )
    provider = _provider()
    provider._client.messages.count_tokens = _CountTokens(4000)  # type: ignore[assignment]
    with caplog.at_level("ERROR"):
        await provider._request_kwargs(
            model="claude-opus-5",
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=100,
            system=_big(800),
        )
    assert any("cache" in r.message.lower() for r in caplog.records)


# ---------------------------------------------------------------------------
# The live count_tokens measurement
# ---------------------------------------------------------------------------

async def test_span_measurement_is_cached_per_session() -> None:
    """"Once per session, cached" — the design's wording, made assertable.

    The frozen prompt (D01.1) does not change within a session, so measuring it
    every turn would spend a network round-trip to learn the same number.
    """
    provider = _provider()
    counter = _CountTokens(4000)
    provider._client.messages.count_tokens = counter  # type: ignore[assignment]

    for _ in range(3):
        await provider._request_kwargs(
            model="claude-opus-5",
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=100,
            tools=_tools(),
            system=_big(800),
        )
    # Two calls total: one for the tools span, one for tools+system. Not six.
    assert len(counter.calls) == 2


async def test_span_measurement_failure_falls_back_to_the_char_estimate(caplog: Any) -> None:
    """A gateway without count_tokens must still get markers, not an exception."""
    async def _no_such_endpoint(**_kwargs: Any) -> Any:
        raise RuntimeError("404 — count_tokens not implemented on this gateway")

    provider = _provider()
    provider._client.messages.count_tokens = _no_such_endpoint  # type: ignore[assignment]
    with caplog.at_level("ERROR"):
        kwargs = await provider._request_kwargs(
            model="claude-opus-5",
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=100,
            tools=_tools(),
            system=_big(800),
        )
    # The char estimate says these spans are large, so markers are still placed.
    assert len(_markers(kwargs)) >= 2
    assert any("count_tokens" in r.message for r in caplog.records)


async def test_the_measurement_can_veto_a_marker_the_estimate_would_place() -> None:
    """The live measurement is the AUTHORITY; the char estimate is the fallback."""
    provider = _provider()
    provider._client.messages.count_tokens = _CountTokens(12)  # type: ignore[assignment]
    kwargs = await provider._request_kwargs(
        model="claude-opus-5",
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=100,
        tools=_tools(),
        system=_big(800),
    )
    assert _markers(kwargs) == []


# ---------------------------------------------------------------------------
# The reader half — populate what D01.6 has accepted since base.py:158
# ---------------------------------------------------------------------------

async def test_cached_input_tokens_are_recorded_from_the_tool_loop_response() -> None:
    """The reader half existed and was never fed: _record_cost has accepted
    cached_input_tokens since D01.6, and Anthropic passed 0 every time."""
    provider = _provider()
    tracker = _RecordingTracker()
    provider.set_cost_tracker(tracker)  # type: ignore[arg-type]

    class _Usage:
        input_tokens = 5000
        output_tokens = 120
        cache_read_input_tokens = 3200
        cache_creation_input_tokens = 900

    response = type("R", (), {"usage": _Usage(), "model": "claude-opus-5"})()
    await provider._record_usage_safe(response, 42.0)

    assert len(tracker.records) == 1
    assert tracker.records[0]["cached_input_tokens"] == 3200


async def test_cached_input_tokens_are_recorded_from_the_stream_response() -> None:
    provider = _provider()
    tracker = _RecordingTracker()
    provider.set_cost_tracker(tracker)  # type: ignore[arg-type]

    class _Usage:
        input_tokens = 800
        output_tokens = 60
        cache_read_input_tokens = 640
        cache_creation_input_tokens = 0

    class _Stream:
        async def get_final_message(self) -> Any:
            return type("M", (), {"usage": _Usage(), "model": "claude-opus-5"})()

    await provider._record_stream_usage_safe(_Stream(), "claude-opus-5", 17.0)

    assert len(tracker.records) == 1
    assert tracker.records[0]["cached_input_tokens"] == 640


# ---------------------------------------------------------------------------
# The ordering invariant — marking must be the LAST mutation before the wire.
#
# Learned from the reference platform's own test suite, which locks this by
# INSPECTING SOURCE ORDER (asserting the marking call appears after every
# normalisation call in the conversation loop). The hazard is real: marking turns
# a string into a block list, so any later pass written as `isinstance(content,
# str)` silently skips marked messages. The same message would then be sent
# normalised on the turn it is unmarked and raw on the turn it is marked —
# breaking the byte-identical prefix the breakpoints exist to protect.
#
# We assert the OUTCOME instead of the source order: what reaches the SDK carries
# the markers. That is stronger, and it holds structurally here rather than by
# convention, because marking lives at the chokepoint immediately before the call
# — there is no "later pass" for it to be reordered against.
# ---------------------------------------------------------------------------

async def test_markers_survive_intact_to_the_sdk_call(monkeypatch: Any) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    class _ScriptedMessages:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []
            self.count_tokens = _CountTokens(4000)

        async def create(self, **kwargs: Any) -> Any:
            self.calls.append(kwargs)
            usage = type("U", (), {"input_tokens": 10, "output_tokens": 2})()
            block = type("B", (), {"text": "ok", "type": "text"})()
            return type("R", (), {
                "content": [block], "usage": usage,
                "model": "claude-opus-5", "stop_reason": "end_turn",
            })()

    provider = _provider()
    scripted = _ScriptedMessages()
    provider._client.messages = scripted  # type: ignore[assignment]

    await provider.complete(
        [Message(role="system", content=_big(900)),
         Message(role="user", content="hello")],
        model="claude-opus-5",
    )

    assert len(scripted.calls) == 1
    sent = scripted.calls[0]
    # The markers are on the wire, not merely computed and dropped.
    assert _markers(sent) != []
    assert len(_markers(sent)) <= 4
    # And the system prompt reached the SDK as a BLOCK LIST, which is the shape
    # change that would hide it from any later string-shaped pass.
    assert isinstance(sent["system"], list)


class _RecordingProbeStore:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    async def record(self, **kwargs: Any) -> None:
        self.records.append(kwargs)


async def test_a_confirmed_response_records_a_probe() -> None:
    provider = _provider()
    provider.set_cost_tracker(_RecordingTracker())  # type: ignore[arg-type]
    probes = _RecordingProbeStore()
    provider.set_cache_probe_store(probes)  # type: ignore[arg-type]

    class _Usage:
        input_tokens = 5000
        output_tokens = 120
        cache_read_input_tokens = 0
        cache_creation_input_tokens = 2712

    response = type("R", (), {"usage": _Usage(), "model": "claude-opus-5"})()
    await provider._record_usage_safe(response, 42.0)

    assert len(probes.records) == 1
    assert probes.records[0]["cache_creation_tokens"] == 2712
    assert probes.records[0]["provider_name"] == "anthropic-main"


async def test_the_provider_reports_a_zero_faithfully_and_the_store_drops_it() -> None:
    """I5 lives in ONE place — the store — and this pins which place that is.

    The provider's job is to report what the endpoint said, honestly, including a
    zero. The store's job is to refuse to persist it. Putting the guard at the
    call site instead would mean every future caller has to remember it, which is
    exactly how a "disable itself forever" bug gets reintroduced.
    """
    provider = _provider()
    provider.set_cost_tracker(_RecordingTracker())  # type: ignore[arg-type]
    probes = _RecordingProbeStore()
    provider.set_cache_probe_store(probes)  # type: ignore[arg-type]

    class _Usage:
        input_tokens = 5000
        output_tokens = 120

    response = type("R", (), {"usage": _Usage(), "model": "claude-opus-5"})()
    await provider._record_usage_safe(response, 42.0)

    # Reported faithfully, not suppressed at the provider ...
    assert len(probes.records) == 1
    assert probes.records[0]["cache_creation_tokens"] == 0
    assert probes.records[0]["cache_read_tokens"] == 0
    # ... and test_a_zero_reading_is_never_persisted (test_cache_probe_store.py)
    # is what proves the real store refuses it.


async def test_the_stream_path_records_a_probe_too() -> None:
    """Found in cleanup: the stream path is MARKED but was not instrumented.

    stream() routes through the same chokepoint as the three create() sites, so
    its requests carry markers — but only _record_usage_safe fed the probe store.
    A streaming deployment would therefore have placed markers, been honoured,
    and recorded no evidence of it: the endpoint would read "never confirmed"
    forever. Asymmetric instrumentation is what makes a measurement silently
    blind, which is the whole failure D01.6 exists to prevent.
    """
    provider = _provider()
    provider.set_cost_tracker(_RecordingTracker())  # type: ignore[arg-type]
    probes = _RecordingProbeStore()
    provider.set_cache_probe_store(probes)  # type: ignore[arg-type]

    class _Usage:
        input_tokens = 800
        output_tokens = 60
        cache_read_input_tokens = 640
        cache_creation_input_tokens = 128

    class _Stream:
        async def get_final_message(self) -> Any:
            return type("M", (), {"usage": _Usage(), "model": "claude-opus-5"})()

    await provider._record_stream_usage_safe(_Stream(), "claude-opus-5", 17.0)

    assert len(probes.records) == 1
    assert probes.records[0]["cache_creation_tokens"] == 128
    assert probes.records[0]["cache_read_tokens"] == 640


async def test_the_stream_path_emits_the_result_line(caplog: Any) -> None:
    """The INFO line is how "is caching working?" gets answered from the JSONL.
    A streaming deployment must not be the one that cannot answer it."""
    provider = _provider()
    provider.set_cost_tracker(_RecordingTracker())  # type: ignore[arg-type]

    class _Usage:
        input_tokens = 800
        output_tokens = 60
        cache_read_input_tokens = 640
        cache_creation_input_tokens = 0

    class _Stream:
        async def get_final_message(self) -> Any:
            return type("M", (), {"usage": _Usage(), "model": "claude-opus-5"})()

    with caplog.at_level("INFO"):
        await provider._record_stream_usage_safe(_Stream(), "claude-opus-5", 17.0)

    assert any(r.message == "[cache] breakpoints: result" for r in caplog.records)


async def test_a_probe_write_failure_never_breaks_the_turn() -> None:
    """Fail-open, like every other measurement seam in this provider."""
    class _Exploding:
        async def record(self, **_kwargs: Any) -> None:
            raise RuntimeError("probe store is down")

    provider = _provider()
    tracker = _RecordingTracker()
    provider.set_cost_tracker(tracker)  # type: ignore[arg-type]
    provider.set_cache_probe_store(_Exploding())  # type: ignore[arg-type]

    class _Usage:
        input_tokens = 5000
        output_tokens = 120
        cache_creation_input_tokens = 2712

    response = type("R", (), {"usage": _Usage(), "model": "claude-opus-5"})()
    await provider._record_usage_safe(response, 42.0)  # must not raise
    # And the cost was still recorded — the probe is the optional half.
    assert len(tracker.records) == 1


async def test_a_response_without_cache_fields_records_zero_not_an_error() -> None:
    """D01.6's I4 — a gateway that strips usage cache fields reads 0 HONESTLY.

    0 means "no cache hit" OR "this backend reports nothing". It is never an
    error, and per I5 it is never persisted as evidence the markers are dead.
    """
    provider = _provider()
    tracker = _RecordingTracker()
    provider.set_cost_tracker(tracker)  # type: ignore[arg-type]

    class _Usage:
        input_tokens = 100
        output_tokens = 10

    response = type("R", (), {"usage": _Usage(), "model": "claude-opus-5"})()
    await provider._record_usage_safe(response, 5.0)

    assert tracker.records[0]["cached_input_tokens"] == 0
