"""The gate must DO the thing, not confess it (Bakir, 2026-08-16).

"I do not want agent to record, I want agent to do and tell me done."

MEASURED over 7 days before this was written: the overclaim gate caught 11 false
claims, CORRECTED 1, and fulfilled 0. The other 10 became "I couldn't fully
complete this…" messages. The reason is structural, not accidental — only two
culprits had an actuator:

* ``retrieval``          -> ``_try_corrective_rerun`` (read-only, so safe)
* ``scheduling_commit``  -> ``_try_fulfill_schedule_commit``

Everything else fell to the honest floor. 8 of the 11 named ``owl_build``.

WHY WRITE-EFFECT CULPRITS WERE EXCLUDED, AND WHY THAT CAN NOW CHANGE.
``_try_corrective_rerun`` is documented "skipped for write-effect culprits by the
callers" because re-running a write could commit the side effect twice. That is a
real hazard and it is not being waived here.

What changes is that the hazard can now be MEASURED away. ``unverified_effects``
lumps ``verified is False`` (the tool's own ``verify()`` observed the effect to be
ABSENT) together with ``verified is None`` (no opinion). Only the first is safe:
you cannot double an effect that has been measured not to exist. So the snapshot
now records the two separately, and the actuator fires ONLY on measured absence.
``unknown`` keeps the floor exactly as before — the burden of proof stays on the
claim.

This is the same primitive the programme already built (``ToolResult.verified``
tri-state) finally being used for something other than blocking: it is what makes
"do it for real" safe rather than reckless.
"""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.pipeline import delivery_gate as dg
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import ResponseChunk

pytestmark = pytest.mark.asyncio


def _state(**kw: object) -> PipelineState:
    base: dict[str, object] = dict(
        trace_id="t-act",
        session_key="s",
        input_text="rename the secretary to Friday",
        channel="telegram",
        owl_name="secretary",
        pipeline_step="execute",
        turn_made_progress=True,
        no_progress_tools=(),
        consequential_failures=(),
        consequential_snapshot_taken=True,
        delivered_successes=(),
    )
    base.update(kw)
    return PipelineState(**base)  # type: ignore[arg-type]


def _draft(text: str = "Done — I renamed the secretary to Friday.") -> ResponseChunk:
    return ResponseChunk(
        content=text, is_final=False, chunk_index=0, trace_id="t-act", owl_name="secretary"
    )


@pytest.fixture
def spy(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Record whether the corrective rerun was attempted, and with what reason."""
    seen: dict[str, Any] = {"called": 0, "reason": None, "result": None}

    async def _fake(state: PipelineState, correction: str):  # type: ignore[no-untyped-def]
        # Mirror the REAL helper's own bound: "a correction is never corrected
        # again" is enforced inside _try_corrective_rerun, so a double that
        # ignored it would pass a test the system fails.
        if state.corrective_replay:
            return None
        seen["called"] += 1
        seen["reason"] = correction
        return seen["result"]

    monkeypatch.setattr(dg, "_try_corrective_rerun", _fake)
    return seen


class TestAMeasuredAbsentEffectIsRedoneNotConfessed:
    async def test_it_attempts_the_action_instead_of_flooring(
        self, spy: dict[str, Any]
    ) -> None:
        """The live case: owl_build claimed a rename, verify() MEASURED that the
        effect is absent. Nothing landed, so redoing it cannot double anything."""
        spy["result"] = (_draft("Renamed. The secretary now answers as Friday."),)
        state = _state(
            responses=(_draft(),),
            unverified_effects=("owl_build",),
            effects_measured_absent=("owl_build",),
        )

        out = await dg.surface_overclaim_gate(state)

        assert spy["called"] == 1, "the gate confessed instead of acting"
        assert "owl_build" in (spy["reason"] or ""), "the actuator was not told what to redo"
        text = "".join(c.content for c in out.responses)
        assert "Friday" in text
        assert not out.overclaim_blocked, "a fulfilled claim must not be blocked"

    async def test_a_failed_redo_still_falls_back_to_the_honest_floor(
        self, spy: dict[str, Any]
    ) -> None:
        """Trying and failing must not become a new way to lie."""
        spy["result"] = None
        state = _state(
            responses=(_draft(),),
            unverified_effects=("owl_build",),
            effects_measured_absent=("owl_build",),
        )

        out = await dg.surface_overclaim_gate(state)

        assert spy["called"] == 1
        assert out.overclaim_blocked is True
        assert any(c.is_floor for c in out.responses)


class TestUnknownStaysUnsafe:
    async def test_an_UNKNOWN_effect_is_never_redone(self, spy: dict[str, Any]) -> None:
        """The safety boundary, pinned. verified=None means nobody observed whether
        the side effect landed. Re-running could commit it twice, so the floor
        stands exactly as it did before this change.

        If someone later widens the actuator to all of `unverified_effects`, this
        test goes red — which is the point.
        """
        state = _state(
            responses=(_draft(),),
            unverified_effects=("send_message",),
            effects_measured_absent=(),  # verify() had NO opinion
        )

        out = await dg.surface_overclaim_gate(state)

        assert spy["called"] == 0, "a write with an UNKNOWN outcome was re-run — unsafe"
        assert out.overclaim_blocked is True

    async def test_a_correction_is_never_itself_corrected(
        self, spy: dict[str, Any]
    ) -> None:
        """Bounded: the corrective child must not spawn its own corrective child."""
        spy["result"] = (_draft("second try"),)
        state = _state(
            responses=(_draft(),),
            unverified_effects=("owl_build",),
            effects_measured_absent=("owl_build",),
            corrective_replay=True,
        )

        out = await dg.surface_overclaim_gate(state)

        assert out.overclaim_blocked is True


class TestTheSnapshotSeparatesMeasuredFromUnknown:
    async def test_only_verified_False_counts_as_measured_absent(self) -> None:
        """The snapshot is where the two are told apart; everything above depends
        on it. verified=False -> measured absent. verified=None -> unknown."""
        from stackowl.pipeline.steps.execute import _measured_absent_effects

        class _O:
            def __init__(self, name: str, verified: bool | None) -> None:
                self.name = name
                self.verified = verified
                self.effect_class = "creates_persistent_entity"
                self.side_effect_committed = True

        got = _measured_absent_effects(
            [_O("owl_build", False), _O("send_message", None), _O("cronjob", True)]
        )

        assert got == ("owl_build",)

    async def test_an_effect_that_committed_nothing_is_not_measured_absent(self) -> None:
        """Mirrors unverified_effects' own guard: a rejected/read-only action never
        claimed an effect, so it is not something to go and redo."""
        from stackowl.pipeline.steps.execute import _measured_absent_effects

        class _O:
            name = "cronjob"
            verified = False
            effect_class = "schedules"
            side_effect_committed = False

        assert _measured_absent_effects([_O()]) == ()
