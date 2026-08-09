"""D01.1 slice 3 — assemble carries the STABLE profile, not per-turn recall.

Measured on the live platform (2026-07-27), per-turn `memory_context` varied in
every session observed, while `base` sat unchanged at 3768 chars throughout. It
is the single largest source of system-prompt instability, and an unstable prompt
silently forfeits the provider's automatic prefix cache.

Bakir accepted the recall trade explicitly (`recall_risk: ACCEPTED — memory is
weak today, so a regression is tolerable; the profile + tool is expected to be a
net gain`). Depth does not disappear: the `memory` tool is registered and the
model calls it when a conversation needs more than the profile.

`classify` still builds `memory_context` — `execute` reads it for grounding
haystacks — so this slice changes what reaches the PROMPT, not what the pipeline
computes. Taking that work off the critical path is a separate latency slice.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stackowl.pipeline.services import StepServices, set_services
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps import assemble

pytestmark = pytest.mark.asyncio


def _state(**kw: object) -> PipelineState:
    base = dict(
        trace_id="t-prof-1",
        session_key="owl:secretary:cli:dm:1",
        input_text="hi",
        channel="cli",
        owl_name="secretary",
        pipeline_step="assemble",
    )
    base.update(kw)
    return PipelineState(**base)  # type: ignore[arg-type]


async def test_the_profile_reaches_the_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The check D01.1 SHOULD have had, now against the real writer.

    D01.1 shipped a reader for a file nothing wrote, and this test passed
    throughout by writing that file itself — so it proved the reader worked while
    the live prompt carried nothing at all. It now goes through CuratedMemory,
    which is what actually creates the file in production.
    """
    from stackowl.memory.curated import USER_TARGET, CuratedMemory, shared_memory

    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path))
    monkeypatch.setattr(
        "stackowl.memory.curated._SHARED", CuratedMemory(root=tmp_path / "memory"),
    )
    shared_memory().add(
        USER_TARGET, "PROFILE_MARKER: Bakir builds StackOwl.", "permanent",
    )
    set_services(StepServices())

    out = await assemble.run(_state())

    assert "PROFILE_MARKER" in (out.system_prompt or "")


async def test_owl_notes_reach_the_prompt_alongside_the_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D08.1 — the per-owl half. Two blocks, labelled, because "who my user is"
    and "what I learned doing my job" are different claims."""
    from stackowl.memory.curated import USER_TARGET, CuratedMemory, shared_memory

    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path))
    monkeypatch.setattr(
        "stackowl.memory.curated._SHARED", CuratedMemory(root=tmp_path / "memory"),
    )
    shared_memory().add(USER_TARGET, "PROFILE_MARKER: the user.", "permanent")
    shared_memory().add("secretary", "NOTES_MARKER: how I work.", "permanent")
    set_services(StepServices())

    out = await assemble.run(_state())

    prompt = out.system_prompt or ""
    assert "PROFILE_MARKER" in prompt
    assert "NOTES_MARKER" in prompt


async def test_per_turn_recall_no_longer_reaches_the_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The change that makes the prompt freezable. memory_context is still on
    the state for execute's grounding haystacks — it just stops being prompt
    text, because it is what varies every turn."""
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path))
    set_services(StepServices())

    out = await assemble.run(_state(memory_context="RECALL_MARKER: varies per turn"))

    assert "RECALL_MARKER" not in (out.system_prompt or "")
    # Still available to the rest of the pipeline — moved, not deleted.
    assert out.memory_context == "RECALL_MARKER: varies per turn"


async def test_two_turns_with_different_recall_produce_the_same_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Invariant I1, at the level this slice can prove it: the part that used to
    differ between turns no longer does. Same profile, different recall, same
    prompt."""
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path))
    (tmp_path / "USER.md").write_text("stable facts", encoding="utf-8")
    set_services(StepServices())

    first = await assemble.run(_state(memory_context="turn one recalled A"))
    second = await assemble.run(_state(memory_context="turn two recalled B"))

    assert first.system_prompt == second.system_prompt


async def test_no_profile_file_still_produces_a_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Invariant I2 — most installs have never written a USER.md, and that must
    be an ordinary turn rather than a degraded one."""
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path))
    set_services(StepServices())

    out = await assemble.run(_state())

    assert out.system_prompt  # the charter and capability text still land
