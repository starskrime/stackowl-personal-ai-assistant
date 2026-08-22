"""DEBT-21 — cost_records must record WHICH OWL spent, or I1 cannot be measured.

D01.1's pass/fail gate is stated as "within one conversation_id, every turn sends a
byte-identical system prompt", measured by
``COUNT(DISTINCT prompt_hash) GROUP BY conversation_id``. That query counts a CORRECT
design as a violation: a lane can run several owls — the staged RCA drives
rca_gatherer, hypothesis and verifier against one incident lane, and the live
prompts show persona_len cycling 291 / 252 / 255 / 303 inside a single
incarnation. Different owls MUST have different prompts; that is invariant I6 and
the entire reason the prompt key is (session_key, owl_name).

Bakir chose (2026-07-27) to add the column rather than infer the owl from
session_key. session_key encodes the owl for anything that came from ingress, but
NOT for internal lanes — the RCA's key is a bare incident id shared by three
owls — so inference would have been right for conversations and silently wrong
for exactly the lanes that exposed the problem.

No provider signature grows: TraceContext already carries owl_name, and
``_record_cost`` already reads session_key and conversation_id off it. Same seam
D01.6 found, for the same reason.
"""

from __future__ import annotations

from stackowl.db.pool import DbPool
from stackowl.events.bus import EventBus
from stackowl.providers.cost_tracker import CostTracker

LANE = "incident-726f65e45b9f"   # an RCA lane: one key, three owls
RUN = "20260727_040000_abcd1234"


async def _spend(tracker: CostTracker, owl: str, trace: str) -> None:
    await tracker.record(
        provider_name="acme", model="acme-v1",
        input_tokens=100, output_tokens=10, duration_ms=1.0, trace_id=trace,
        session_key=LANE, conversation_id=RUN, owl_name=owl,
        prompt_hash=f"hash-for-{owl}",
    )


async def test_the_owl_that_spent_is_recorded(tmp_db: DbPool) -> None:
    tracker = CostTracker(db=tmp_db, event_bus=EventBus(), daily_limit_usd=None)

    await _spend(tracker, "rca_gatherer", "t1")

    rows = await tmp_db.fetch_all("SELECT owl_name FROM cost_records")
    assert [r["owl_name"] for r in rows] == ["rca_gatherer"]


async def test_i1_can_now_tell_three_owls_apart_on_one_lane(tmp_db: DbPool) -> None:
    """The whole point. Three owls share ONE incarnation and each has its own
    prompt — correct by I6. Grouped by conversation_id alone that reads as three
    violations; grouped WITH the owl it reads as three clean conversations."""
    tracker = CostTracker(db=tmp_db, event_bus=EventBus(), daily_limit_usd=None)
    for i, owl in enumerate(("rca_gatherer", "hypothesis", "verifier")):
        await _spend(tracker, owl, f"t{i}")

    naive = await tmp_db.fetch_all(
        "SELECT COUNT(DISTINCT prompt_hash) d FROM cost_records "
        "WHERE conversation_id = ? GROUP BY conversation_id", (RUN,),
    )
    honest = await tmp_db.fetch_all(
        "SELECT COUNT(DISTINCT prompt_hash) d FROM cost_records "
        "WHERE conversation_id = ? GROUP BY conversation_id, owl_name", (RUN,),
    )

    assert naive[0]["d"] == 3, "the old gate sees three prompts and calls it broken"
    assert [r["d"] for r in honest] == [1, 1, 1], "each owl is individually stable"


async def test_the_same_owl_drifting_is_still_caught(tmp_db: DbPool) -> None:
    """The gate must not become unfalsifiable. Grouping by owl fixes a false
    POSITIVE; it must not hide a real drift within one owl."""
    tracker = CostTracker(db=tmp_db, event_bus=EventBus(), daily_limit_usd=None)
    await tracker.record(
        provider_name="acme", model="acme-v1", input_tokens=1, output_tokens=1,
        duration_ms=1.0, trace_id="t1", session_key=LANE, conversation_id=RUN,
        owl_name="secretary", prompt_hash="hash-A",
    )
    await tracker.record(
        provider_name="acme", model="acme-v1", input_tokens=1, output_tokens=1,
        duration_ms=1.0, trace_id="t2", session_key=LANE, conversation_id=RUN,
        owl_name="secretary", prompt_hash="hash-B",   # drifted!
    )

    rows = await tmp_db.fetch_all(
        "SELECT COUNT(DISTINCT prompt_hash) d FROM cost_records "
        "WHERE conversation_id = ? GROUP BY conversation_id, owl_name", (RUN,),
    )
    assert rows[0]["d"] == 2, "a genuine drift within one owl must still fail I1"


async def test_an_unattributed_call_records_empty_not_a_crash(tmp_db: DbPool) -> None:
    """Background and utility calls may have no owl in context. That must cost
    the row its attribution, never the recording."""
    tracker = CostTracker(db=tmp_db, event_bus=EventBus(), daily_limit_usd=None)

    await tracker.record(
        provider_name="acme", model="acme-v1", input_tokens=1, output_tokens=1,
        duration_ms=1.0, trace_id="t1",
    )

    rows = await tmp_db.fetch_all("SELECT owl_name FROM cost_records")
    assert rows[0]["owl_name"] == ""
