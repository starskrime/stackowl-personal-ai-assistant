"""The deliver step enforces stored output prefs on the response (channel-agnostic)."""

from __future__ import annotations

import json
from typing import Literal

from stackowl.memory.preferences import GLOBAL_OWNER_KEY
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps.deliver import _enforce_output_prefs, _summarize_if_terse
from stackowl.pipeline.streaming import ResponseChunk
from stackowl.providers.base import CompletionResult, Message, ModelProvider

_TABLE = "Data:\n\n| Name | Age |\n| --- | --- |\n| Bob | 3 |\n"


class _FakeProvider(ModelProvider):
    """Minimal ModelProvider stand-in for the length_terse summarizer call."""

    def __init__(self, canned: str, *, raise_on_complete: Exception | None = None) -> None:
        self._canned = canned
        self._raise = raise_on_complete
        self.calls: list[list[Message]] = []

    @property
    def name(self) -> str:
        return "fake-fast"

    @property
    def protocol(self) -> Literal["openai", "anthropic", "gemini"]:
        return "openai"

    async def complete(self, messages: list[Message], model: str, **kwargs: object) -> CompletionResult:
        self.calls.append(list(messages))
        if self._raise is not None:
            raise self._raise
        return CompletionResult(
            content=self._canned, input_tokens=1, output_tokens=1,
            model="fake-model", provider_name=self.name, duration_ms=1.0,
        )

    async def stream(self, messages: list[Message], model: str, **kwargs: object):  # pragma: no cover
        yield ""


class _FakeRegistry:
    def __init__(self, provider: ModelProvider | None) -> None:
        self._provider = provider

    def get_by_tier(self, tier: str) -> tuple[ModelProvider, str]:
        assert self._provider is not None
        return self._provider, "fake-fast-model"


def _state_for_terse() -> PipelineState:
    trace_id = "t-terse"
    return PipelineState(
        trace_id=trace_id, session_id="local", input_text="x", channel="cli",
        owl_name="secretary", pipeline_step="deliver",
    )


class _PrefStore:
    def __init__(self, prefs: dict[str, str]) -> None:
        self._prefs = prefs

    async def list_for_owner(self, owner_key: str) -> dict[str, str]:
        return dict(self._prefs)


class _ScopedPrefStore:
    """Distinguishes the GLOBAL sentinel from per-channel owner_keys."""

    def __init__(self, by_owner: dict[str, dict[str, str]]) -> None:
        self._by_owner = by_owner

    async def list_for_owner(self, owner_key: str) -> dict[str, str]:
        return dict(self._by_owner.get(owner_key, {}))


def _state_with_table() -> PipelineState:
    chunk = ResponseChunk(
        content=_TABLE, is_final=False, chunk_index=0, trace_id="t", owl_name="secretary",
    )
    return PipelineState(
        trace_id="t", session_id="local", input_text="x", channel="cli",
        owl_name="secretary", pipeline_step="deliver", responses=(chunk,),
    )


async def test_enforces_no_tables_preference() -> None:
    services = StepServices(preference_store=_PrefStore({"output_tables": "off"}))  # type: ignore[arg-type]
    out = await _enforce_output_prefs(_state_with_table(), services)
    body = "".join(c.content for c in out.responses)
    assert "|" not in body and "```" not in body
    assert "Name: Bob" in body


async def test_no_preference_is_byte_identical() -> None:
    services = StepServices(preference_store=_PrefStore({}))  # type: ignore[arg-type]
    state = _state_with_table()
    out = await _enforce_output_prefs(state, services)
    assert out.responses == state.responses  # untouched


async def test_no_store_is_byte_identical() -> None:
    state = _state_with_table()
    out = await _enforce_output_prefs(state, StepServices())
    assert out.responses == state.responses


async def test_global_preference_enforced_when_owner_key_empty() -> None:
    """A GLOBALLY-set output_tables=off is enforced even though the turn's
    owner_key ('local') has no per-owner pref — proves cross-channel scope."""
    store = _ScopedPrefStore({GLOBAL_OWNER_KEY: {"output_tables": "off"}})
    services = StepServices(preference_store=store)  # type: ignore[arg-type]
    out = await _enforce_output_prefs(_state_with_table(), services)
    body = "".join(c.content for c in out.responses)
    assert "|" not in body and "```" not in body
    assert "Name: Bob" in body


async def test_owner_pref_overrides_global() -> None:
    """A per-owner output_tables=on overrides a global =off (tables kept)."""
    store = _ScopedPrefStore({
        GLOBAL_OWNER_KEY: {"output_tables": "off"},
        "local": {"output_tables": "on"},
    })
    services = StepServices(preference_store=store)  # type: ignore[arg-type]
    state = _state_with_table()
    out = await _enforce_output_prefs(state, services)
    assert out.responses == state.responses  # untouched — tables allowed


# --------------------------------------------------------------------------- #
# length=terse — the real delivery-seam summarizer (_summarize_if_terse)      #
# --------------------------------------------------------------------------- #

async def test_short_text_skips_summarizer_entirely() -> None:
    """Below the skip threshold, no provider call is made at all."""
    provider = _FakeProvider("should never be used")
    services = StepServices(provider_registry=_FakeRegistry(provider))  # type: ignore[arg-type]
    text = "a short reply"
    out = await _summarize_if_terse(text, services, _state_for_terse())
    assert out == text
    assert provider.calls == []


async def test_long_text_gets_compressed() -> None:
    long_text = "word " * 200  # well over the skip threshold
    provider = _FakeProvider("a much shorter summary")
    services = StepServices(provider_registry=_FakeRegistry(provider))  # type: ignore[arg-type]
    out = await _summarize_if_terse(long_text, services, _state_for_terse())
    assert out == "a much shorter summary"
    assert len(provider.calls) == 1


async def test_provider_failure_falls_back_to_full_text() -> None:
    long_text = "word " * 200
    provider = _FakeProvider("", raise_on_complete=RuntimeError("boom"))
    services = StepServices(provider_registry=_FakeRegistry(provider))  # type: ignore[arg-type]
    out = await _summarize_if_terse(long_text, services, _state_for_terse())
    assert out == long_text  # never dropped content on failure


async def test_empty_summary_falls_back_to_full_text() -> None:
    long_text = "word " * 200
    provider = _FakeProvider("   ")  # whitespace-only → treated as empty
    services = StepServices(provider_registry=_FakeRegistry(provider))  # type: ignore[arg-type]
    out = await _summarize_if_terse(long_text, services, _state_for_terse())
    assert out == long_text


async def test_no_provider_registry_falls_back_to_full_text() -> None:
    long_text = "word " * 200
    out = await _summarize_if_terse(long_text, StepServices(), _state_for_terse())
    assert out == long_text


async def test_enforce_output_prefs_runs_terse_summarizer_end_to_end() -> None:
    """length=terse in the stored output_style triggers real summarization at
    the delivery seam, composed with the existing deterministic enforcement."""
    long_text = "word " * 200
    chunk = ResponseChunk(
        content=long_text, is_final=False, chunk_index=0, trace_id="t", owl_name="secretary",
    )
    state = PipelineState(
        trace_id="t", session_id="local", input_text="x", channel="cli",
        owl_name="secretary", pipeline_step="deliver", responses=(chunk,),
    )
    provider = _FakeProvider("compressed reply")
    prefs = {"output_style": json.dumps({"length": "terse"})}
    services = StepServices(  # type: ignore[arg-type]
        preference_store=_PrefStore(prefs),
        provider_registry=_FakeRegistry(provider),
    )
    out = await _enforce_output_prefs(state, services)
    body = "".join(c.content for c in out.responses)
    assert body == "compressed reply"
    assert len(provider.calls) == 1
