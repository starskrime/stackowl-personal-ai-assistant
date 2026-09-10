"""Two different facts were reported with one word, and the word meant "not yet".

`[shadow] validate: exit — cold start, insufficient held-out sample` fires 4-8
times a day. It reads as a shortage that will pass. MEASURED 2026-09-10 over the
retained corpus and the live database, per owl, replayable samples in the 14-day
lookback::

    rca_gatherer 155/479   hypothesis 243/427   verifier 192/405
    secretary     73/190   jobmarket    4/108   mailbutler  1/22
    scout          0/8     headhunter   0/4     syshealth   0/3
    archivist      0/1

`scout` has **194 outcomes, 182 of them scored successes, and 0 replayable** — and
has reported `eligible=0` ten separate times. `syshealth` nine times. The number has
never once risen, and it cannot: `_eligible_for_replay` requires an empty
`tool_sequence`, because the replay harness deliberately wires NO tool registry
(that isolation is the whole point — a shadow replay must not cause side effects).
An owl whose work IS tools can therefore never produce an eligible turn.

So for four owls "insufficient held-out sample" does not mean *not yet*. It means
*never*, and it says so in the vocabulary of *not yet*.

WHAT THIS DOES NOT CHANGE, because it is a recorded decision and it stands. The
filter's docstring already weighed reporting a shortage against failing on a
harness artefact and chose the shortage — correctly: a bogus regression verdict
would be worse. The gate still fails CLOSED, still replays nothing, still returns
`cold_start=True`. Only the sentence changes, and only in the case where the two
facts are distinguishable — which they always were, because `outcomes` and
`eligible` are both in hand at that line.

Whether tool-using owls should have a validation path at all is a product decision
and is queued as ESC-167, not taken here.
"""

from __future__ import annotations

import logging
import time

import pytest

from stackowl.db.pool import DbPool
from stackowl.infra.observability import log
from stackowl.memory.outcome_store import TaskOutcomeStore
from stackowl.owls.dna import OwlDNA
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.shadow_validator import ShadowValidator
from stackowl.providers.registry import ProviderRegistry

_OWL = "tool_using_owl"


def _manifest() -> OwlAgentManifest:
    return OwlAgentManifest(
        name=_OWL, role="test-owl", system_prompt="You are a test owl.",
        model_tier="standard", dna=OwlDNA(),
    )


async def _seed(db: DbPool, *, n: int, tools: tuple[str, ...]) -> None:
    """Scored, successful outcomes — with or without a tool_sequence."""
    store = TaskOutcomeStore(db)
    for i in range(n):
        trace_id = f"{_OWL}-{'t' if tools else 'p'}-{i}"
        await store.record(
            trace_id=trace_id, session_key="s", owl_name=_OWL, channel="cli",
            success=True, latency_ms=100.0, tool_call_count=len(tools),
            failure_class=None, step_durations={}, input_text=f"in {i}",
            response_text=f"out {i}", tool_sequence=tools,
        )
        row = await store.get_by_trace_id(trace_id)
        assert row is not None
        await store.set_quality_score(row.outcome_id, 0.9)
        await db.execute(
            "UPDATE task_outcomes SET captured_at = ? WHERE trace_id = ?",
            (time.time() + i, trace_id),
        )


class _Records(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.seen: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.seen.append(record)


async def _validate_and_capture(db: DbPool) -> tuple[object, list[logging.LogRecord]]:
    """Run validate() while reading the NAMED logger.

    Not `caplog`: `configure_logging` sets `propagate = False` on `stackowl`, so a
    caplog assertion passes alone and fails in any session that configured logging
    first. That is measured, not hypothetical — it cost an earlier item a full run.
    """
    handler = _Records()
    # AND THE LEVEL HAS TO BE LOWERED, which the first draft of this helper did
    # not do and which read as "the line was never emitted". MEASURED: under
    # pytest `stackowl.owls` has an EFFECTIVE level of 30 (WARNING), so an
    # `info()` call is discarded before any handler sees it. A test that reads
    # INFO must say so.
    previous = log.owls.level
    log.owls.setLevel(logging.INFO)
    log.owls.addHandler(handler)
    try:
        validator = ShadowValidator(db, ProviderRegistry())
        result = await validator.validate(_OWL, _manifest(), OwlDNA())
    finally:
        log.owls.removeHandler(handler)
        log.owls.setLevel(previous)
    return result, handler.seen


@pytest.mark.asyncio
async def test_history_that_can_never_be_replayed_is_not_called_a_cold_start(
    tmp_db: DbPool,
) -> None:
    """THE CASE THAT FIRES DAILY. Twenty scored successes, every one tool-driven —
    `scout`'s exact shape. The old sentence told a reader to wait for a sample that
    can never arrive."""
    await _seed(tmp_db, n=20, tools=("shell",))

    result, records = await _validate_and_capture(tmp_db)

    assert result.passed is False, "the gate must still fail closed"
    assert result.cold_start is True, "the caller's contract must not move"
    assert result.n_replayed == 0

    messages = [r.getMessage() for r in records]
    named = [m for m in messages if "NOT a cold start" in m]
    assert named, (
        f"an owl with 20 scored, 0 replayable outcomes is still being reported as a "
        f"cold start — the word means 'not yet' and this is 'never': {messages}"
    )
    assert "none of it is replayable" in named[0]
    assert all("cold start, insufficient" not in m for m in messages), (
        "both sentences were emitted — a reader gets one fact twice, in two moods"
    )


@pytest.mark.asyncio
async def test_a_GENUINE_cold_start_still_says_cold_start(tmp_db: DbPool) -> None:
    """THE CONTROL, and the half that keeps the distinction worth making.

    Two replayable outcomes against a required five IS a shortage that will pass.
    If the new sentence swallowed this case too, the change would have replaced one
    undifferentiated word with another.
    """
    await _seed(tmp_db, n=2, tools=())

    result, records = await _validate_and_capture(tmp_db)

    assert result.passed is False
    assert result.cold_start is True
    messages = [r.getMessage() for r in records]
    assert any("cold start, insufficient" in m for m in messages), messages
    assert all("NOT a cold start" not in m for m in messages), (
        "a real shortage is now reported as permanent — the opposite error"
    )


@pytest.mark.asyncio
async def test_no_history_at_all_is_a_cold_start_too(tmp_db: DbPool) -> None:
    """An owl with NOTHING is the original cold start: `outcomes` is empty, so the
    structural branch must not claim its history is unreplayable — it has none."""
    result, records = await _validate_and_capture(tmp_db)

    assert result.passed is False
    messages = [r.getMessage() for r in records]
    assert all("NOT a cold start" not in m for m in messages), (
        "an owl with no history was told its history is unreplayable"
    )


@pytest.mark.tripwire
def test_both_sentences_are_reported_at_INFO() -> None:
    """Production writes zero DEBUG, so a distinction drawn below INFO is one no
    reader ever sees — the failure this programme has paid for ten times."""
    import ast
    import inspect
    from pathlib import Path

    from stackowl.owls import shadow_validator

    tree = ast.parse(Path(inspect.getfile(shadow_validator)).read_text(encoding="utf-8"))
    levels: dict[str, str] = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        text = "".join(
            a.value for a in node.args
            if isinstance(a, ast.Constant) and isinstance(a.value, str)
        )
        for fragment in ("cold start, insufficient", "NOT a cold start"):
            if fragment in text:
                levels[fragment] = node.func.attr
    assert set(levels) == {"cold start, insufficient", "NOT a cold start"}, levels
    assert set(levels.values()) <= {"info", "warning", "error"}, (
        f"a refusal is announced below INFO: {levels}"
    )
