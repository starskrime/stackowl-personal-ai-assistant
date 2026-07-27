"""D01.1 slice 5 — the system prompt is built ONCE per session and reused.

The item's whole point, and only reachable now that every part of the prompt is
stable: the banner left (slice 1), per-turn recall left (slice 3), lessons
became query-independent, skills became a catalogue (4b), and the wall-clock
moved to the volatile tier (stage 2). Freezing anything before that would have
pinned whichever value the first turn happened to have.

`assemble` becomes a cache lookup with a cold-start branch. On a hit it does not
probe the model window, score anything, or read the database — those all leave
the critical path of every reply after the first.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stackowl.pipeline.services import StepServices, set_services
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps import assemble

pytestmark = pytest.mark.asyncio

LANE = "owl:secretary:cli:dm:1"
RUN = "20260727_040000_abcd1234"
NEXT_RUN = "20260728_040000_ffff9999"


class _MemoryPromptStore:
    """The real store's contract, in memory, recording how it was used."""

    def __init__(self) -> None:
        self.saved: list[tuple[str, str, str]] = []
        self.loads = 0
        self._rows: dict[tuple[str, str], tuple[str, str, int | None]] = {}

    async def load(self, *, session_key: str, owl_name: str, session_id: str):
        self.loads += 1
        row = self._rows.get((session_key, owl_name))
        if row is None or row[0] != session_id:
            return None
        from stackowl.sessions.prompt_store import StoredPrompt

        return StoredPrompt(
            session_key=session_key, owl_name=owl_name, session_id=row[0],
            prompt_text=row[1], prompt_hash="h", model_window=row[2], built_at="",
        )

    async def save(self, *, session_key: str, owl_name: str, session_id: str,
                   prompt_text: str, model_window: int | None, **_kw: object) -> None:
        self.saved.append((session_key, owl_name, session_id))
        self._rows[(session_key, owl_name)] = (session_id, prompt_text, model_window)


def _state(**kw: object) -> PipelineState:
    base = dict(
        trace_id="t-freeze", session_key=LANE, session_id=RUN, input_text="hi",
        channel="cli", owl_name="secretary", pipeline_step="assemble",
    )
    base.update(kw)
    return PipelineState(**base)  # type: ignore[arg-type]


async def _run(store: _MemoryPromptStore, tmp_path: Path,
               monkeypatch: pytest.MonkeyPatch, **kw: object) -> PipelineState:
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path))
    set_services(StepServices(session_prompt_store=store))
    return await assemble.run(_state(**kw))


async def test_the_second_turn_reuses_the_first_turns_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Invariant I1, as a behaviour rather than a measurement."""
    store = _MemoryPromptStore()

    first = await _run(store, tmp_path, monkeypatch)
    second = await _run(store, tmp_path, monkeypatch, input_text="something else")

    assert first.system_prompt == second.system_prompt
    assert len(store.saved) == 1, "built once, not once per turn"


async def test_a_new_incarnation_rebuilds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rollover must actually change something, or the boundary is cosmetic."""
    store = _MemoryPromptStore()

    await _run(store, tmp_path, monkeypatch)
    await _run(store, tmp_path, monkeypatch, session_id=NEXT_RUN)

    assert len(store.saved) == 2
    assert store.saved[1][2] == NEXT_RUN


async def test_a_different_owl_gets_its_own_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Invariant I6. Not hypothetical: the staged RCA runs three owls against
    one incident lane."""
    store = _MemoryPromptStore()

    await _run(store, tmp_path, monkeypatch, owl_name="secretary")
    await _run(store, tmp_path, monkeypatch, owl_name="scout")

    assert {s[1] for s in store.saved} == {"secretary", "scout"}


async def test_no_store_wired_still_produces_a_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Invariant I2 — the freeze is an enhancement to a working pipeline, never
    a precondition for one. Every settings-less unit test runs this path."""
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path))
    set_services(StepServices())

    out = await assemble.run(_state())

    assert out.system_prompt


async def test_a_turn_with_no_incarnation_is_never_frozen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Background work that never passed through ingress has no session_id.
    Freezing under an empty key would collide every such turn into one prompt."""
    store = _MemoryPromptStore()

    await _run(store, tmp_path, monkeypatch, session_id="")

    assert store.saved == []
