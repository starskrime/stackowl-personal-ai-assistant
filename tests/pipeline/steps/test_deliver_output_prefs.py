"""The deliver step enforces stored output prefs on the response (channel-agnostic)."""

from __future__ import annotations

from stackowl.memory.preferences import GLOBAL_OWNER_KEY
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps.deliver import _enforce_output_prefs
from stackowl.pipeline.streaming import ResponseChunk

_TABLE = "Data:\n\n| Name | Age |\n| --- | --- |\n| Bob | 3 |\n"


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
