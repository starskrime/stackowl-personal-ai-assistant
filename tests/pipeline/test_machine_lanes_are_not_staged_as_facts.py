"""The platform must not learn its own prompts back as facts about the user.

MEASURED 2026-08-25 on the live `staged_facts` table, before it was wiped:

  * 5,212 rows, of which 4,480 (85%) were NOT FACTS. They were the incident RCA's
    own sub-agent system prompts, staged as `source_type='conversation'`:
        "User: You are the VERIFIER owl in a fixed-stage incident root-cause
         analysis. Your ONLY job is to check whether the hypothesis is supported
         by the EVIDENCE BRIEF..."
  * That single prompt appeared 1,505 times. Two more (HYPOTHESIS, EVIDENCE
    GATHERER) accounted for another 688.
  * 66% of the whole table was exact duplicates, concentrated in 50 families —
    because three prompts recur on every incident, forever.

THE MECHANISM. `persist_turn` stores "User: {input_text}\\n\\nAssistant: {...}"
for EVERY pipeline turn. Incident RCA runs its sub-agents through that same
pipeline, and a sub-agent's `input_text` IS the constructed prompt. So the
platform filed its own instructions as things a person said.

AND IT REFILLS IMMEDIATELY. Forty minutes after the table was emptied it held
three rows, ALL THREE prompt-shaped, all from one incident lane. Deleting the
rows without this guard buys about an hour.

THE PREDICATE ALREADY EXISTED. `is_machine_lane()` in sessions/models.py, whose
docstring reads: "Used by the conversation miner to skip lanes that cannot
contain a user fact by construction." It had ZERO CALLERS. Same shape as
`FactReinforcer` (the deduplicator with no callers) and `add_relation` (the graph
writer with no callers) — three things built for exactly this and never wired.

WHY THE GUARD IS HERE AND NOT IN THE BRIDGE. `store()` receives
`owner_scope_key(state)`, which is `identity_key or session_key` — an identity
key when one resolved. So the bridge cannot reliably tell a machine lane from a
person. `state.session_key` is the authoritative lane, and turn_persist is where
"is this turn worth remembering" is decided.
"""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import ResponseChunk
from stackowl.pipeline.turn_persist import persist_turn
from stackowl.sessions.models import MACHINE_LANE_PREFIXES, is_machine_lane

#: Verbatim from the live table — the row that appeared 1,505 times.
REAL_PROMPT = (
    "You are the VERIFIER owl in a fixed-stage incident root-cause analysis. "
    "Your ONLY job is to check whether the hypothesis is supported by the "
    "EVIDENCE BRIEF — not by how confident the hypothesis sounds."
)


class _RecordingStore:
    """Captures what would have been staged."""

    def __init__(self) -> None:
        self.stored: list[tuple[str, str]] = []

    async def store(self, content: str, scope_key: str, **_kw: Any) -> None:
        self.stored.append((content, scope_key))


def _services(store: _RecordingStore) -> Any:
    class _S:
        conversation_store = store
        memory_bridge = None
        retry_queue_store = None
        message_ledger_store = None

    return _S()


def _state(session_key: str, text: str) -> PipelineState:
    return PipelineState(
        trace_id=f"t-{session_key}",
        session_key=session_key,
        input_text=text,
        channel="internal",
        owl_name="secretary",
        pipeline_step="respond",
        responses=(
            ResponseChunk(
                content="an answer", is_final=True, chunk_index=0,
                trace_id=f"t-{session_key}", owl_name="secretary",
            ),
        ),
    )


@pytest.fixture(autouse=True)
def _no_transcript(monkeypatch: pytest.MonkeyPatch) -> None:
    """The transcript is a separate record and is not what this test is about."""
    async def _noop(*_a: object, **_k: object) -> None:
        return None

    monkeypatch.setattr("stackowl.pipeline.turn_persist._record_transcript", _noop)


# ---------------------------------------------------------------------------
# The predicate itself
# ---------------------------------------------------------------------------

def test_the_predicate_recognises_the_real_lanes() -> None:
    """These are the two prefixes the platform mints for its own work."""
    assert is_machine_lane("incident-b5545c2ec371")
    assert is_machine_lane("goal-1234")
    assert MACHINE_LANE_PREFIXES == (
        "goal-", "incident-", "job:", "recover-", "shadow-",
    )


def test_the_predicate_leaves_human_lanes_alone() -> None:
    """A wrong answer here silently drops REAL user facts, which is far worse
    than keeping a prompt — so it keys on our own minted prefixes, never on
    content.

    THE FIXTURE WAS STALE AND HAD TO BE CORRECTED, 2026-08-31. It read
    ``owl:secretary:telegram:72055773`` — a shape ``build_session_key`` has not
    emitted since the chat_type segment was added, and which exists NOWHERE in
    production: scanning every table that carries a session_key found 4,150
    distinct lanes, of which the ``owl:`` ones are 6 five-segment chat lanes
    (segment 3 a ChatType, always) and 109 four-segment runner lanes (104
    ``recovery``, 5 ``objective``). Not one four-segment chat lane exists. A test
    double that stopped resembling the real thing, asserting a shape the builder
    cannot produce."""
    assert not is_machine_lane("owl:secretary:telegram:dm:72055773")
    assert not is_machine_lane("owl:secretary:slack:channel:C0123")
    assert not is_machine_lane("cli")
    assert not is_machine_lane(None)
    assert not is_machine_lane("")


# ---------------------------------------------------------------------------
# The behaviour
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_an_incident_lane_turn_is_not_staged(monkeypatch: pytest.MonkeyPatch) -> None:
    """THE defect. 4,480 rows of this, one of them 1,505 times."""
    store = _RecordingStore()
    monkeypatch.setattr(
        "stackowl.pipeline.turn_persist.get_services", lambda: _services(store)
    )

    await persist_turn(_state("incident-b5545c2ec371", REAL_PROMPT))

    assert store.stored == [], (
        "a sub-agent prompt is the platform talking to itself — filing it as a "
        "fact about the user is how 85% of staged_facts became prompts"
    )


@pytest.mark.asyncio
async def test_a_goal_lane_turn_is_not_staged(monkeypatch: pytest.MonkeyPatch) -> None:
    """The other machine prefix. It had not polluted the table yet, which is
    luck, not design — goal_execution runs the same pipeline."""
    store = _RecordingStore()
    monkeypatch.setattr(
        "stackowl.pipeline.turn_persist.get_services", lambda: _services(store)
    )

    await persist_turn(_state("goal-abc123", "You are executing goal 4."))

    assert store.stored == []


@pytest.mark.asyncio
async def test_a_REAL_user_turn_is_still_staged(monkeypatch: pytest.MonkeyPatch) -> None:
    """The guarantee that must survive. This guard exists to stop noise, and a
    guard that also stops signal is worse than the noise it removed."""
    store = _RecordingStore()
    monkeypatch.setattr(
        "stackowl.pipeline.turn_persist.get_services", lambda: _services(store)
    )

    await persist_turn(
        _state("owl:secretary:telegram:dm:72055773", "my dentist is Dr Antoon in Plano")
    )

    assert len(store.stored) == 1, "a real user turn must still be remembered"
    content, _scope = store.stored[0]
    assert "dentist" in content
