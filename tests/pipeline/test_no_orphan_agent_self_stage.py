"""A failed turn records ONE outcome, not two — and never into a dead store.

MEASURED ON THE LIVE DATABASE 2026-08-14, while working D08.2 slice A:

    staged_facts, by source_type
      agent_self            2,969 rows   ~1,400/day, still climbing
      conversation          2,721 rows   593 scopes, max 60 each — bounded, healthy
      webpage                  10 rows   writer already removed
      conversation_summary      6 rows

`agent_self` is the only one growing without a bound, and it cannot be bounded
the obvious way: every row carries a UNIQUE `source_ref` (the turn's trace_id),
so a per-(source_type, source_ref) trim would delete nothing. That would have
been a write with no effect — the exact shape this programme keeps finding —
which is why the bound was not simply widened.

WHY THE ROWS ARE ORPHANED. `_capture_outcome` stages one low-trust
`agent_self` fact per failed turn. Its own comment says the point is "so recall
(classify.py's lesson_context) can weight it below an RCA-verified skill
lesson". That path is severed in three places, each verified rather than
assumed:

  * `recall()` queries `FROM committed_facts` (sqlite_bridge.py:479), and
    committed_facts holds 0 rows since D08.1's migration 0112.
  * what moved a row from staged to committed was `fact_promoter`, which D08.1
    retired, and the DreamWorker that drove it is unscheduled.
  * `lesson_context.py` turns out to be applied-lesson tracking, a different
    mechanism entirely — it never reads these rows.

So the write happens and the effect does not.

AND THE DATA IS ALREADY KEPT PROPERLY. The staged row re-records, as prose,
four fields the same function has just written to `task_outcomes`:
trace_id, failure_class, input_text and owl_name. `task_outcomes` holds 14,870
rows including 9,220 failures, and `FailureOutcomeMiner` — the actual
single-failure learner — reads THAT store. Two copies of one fact, one of them
unreachable.

Removing the writer therefore loses nothing measurable. This test pins both
halves of that claim: the real store still gets the failure, and the orphaned
duplicate is not written.
"""

from __future__ import annotations

from typing import Any

import pytest


class _RecordingBridge:
    """Records every stage() call so the test can assert one never happens.

    Deliberately records rather than raises: a bridge that raised would also
    make the test pass, and for the wrong reason.
    """

    def __init__(self) -> None:
        self.staged: list[Any] = []

    async def stage(self, fact: Any) -> None:
        self.staged.append(fact)


@pytest.mark.asyncio
async def test_a_failed_turn_records_the_outcome_but_stages_no_duplicate_fact(
    tmp_db: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from stackowl.pipeline.backends import shared
    from stackowl.pipeline.services import StepServices
    from stackowl.pipeline.state import PipelineState, StepError

    bridge = _RecordingBridge()

    # health_loop ON, so the REMOVED write would definitely have fired here.
    # Without this the assertion below could pass vacuously.
    from stackowl.config.settings import Settings

    real_settings = Settings

    class _HealthLoopOn:
        def __init__(self) -> None:
            self._inner = real_settings()

        def __getattr__(self, item: str) -> Any:
            if item == "health_loop":
                return True
            return getattr(self._inner, item)

    monkeypatch.setattr("stackowl.config.settings.Settings", _HealthLoopOn)

    services = StepServices(
        db_pool=tmp_db,
        memory_bridge=bridge,  # type: ignore[arg-type]
    )
    state = PipelineState(
        trace_id="t-orphan",
        session_key="owl:secretary:telegram:dm:1",
        input_text="do the thing",
        channel="telegram",
        owl_name="secretary",
        pipeline_step="execute",
        step_errors=(
            StepError(step="execute", exc_type="ToolCallLeakError", message="boom"),
        ),
        errors=("execute: boom",),
    )

    await shared._capture_outcome(state, 1234.0, services)

    # 1. The REAL learning store got the failure — the path that actually feeds
    #    FailureOutcomeMiner is untouched.
    rows = await tmp_db.fetch_all(
        "SELECT trace_id, success, failure_class FROM task_outcomes WHERE trace_id = ?",
        ("t-orphan",),
    )
    assert rows, "the failure must still reach task_outcomes"
    assert rows[0]["success"] == 0, rows[0]
    assert rows[0]["failure_class"], "a failed turn must carry a failure_class"

    # 2. And nothing was staged into the dead store.
    agent_self = [f for f in bridge.staged if getattr(f, "source_type", "") == "agent_self"]
    assert not agent_self, (
        "a failed turn must not ALSO stage an agent_self fact: recall() reads "
        "committed_facts (empty since migration 0112) and nothing promotes staged "
        f"rows any more, so this write reaches no reader. Staged: {agent_self}"
    )
