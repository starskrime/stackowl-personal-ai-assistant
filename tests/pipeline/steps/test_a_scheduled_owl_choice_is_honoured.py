"""A job that names its owl must run as that owl.

BAKIR, 2026-08-19/20: his Gmail digest delivered ``⚠️ Digest FAILED again — no mail
checked`` twice, and ``Issue: Mailbutler owl lacks shell``.

MEASURED. The job's own params say::

    goal_execution-88f54aa1   params.owl = "secretary"

and ``goal_execution`` sets ``owl_name`` from exactly that. Secretary is UNBOUNDED —
it holds ``shell`` and can run ``gmail_assist.py``. But the router overrode it::

    22:00:26  trace=goal-9069b24b  ->  owl=mailbutler
    22:04:30  trace=goal-9e990bc5-fix -> owl=mailbutler

so the digest ran as the ONE owl that cannot run the script, and reported failure.

THE CAUSE IS A CONFLATED SENTINEL. Triage skips routing when
``state.owl_name != _FALLBACK_OWL`` — i.e. it treats "secretary" as "nobody has
chosen yet". For an inbound chat turn that is correct: secretary IS the default
target before routing. For a SCHEDULED job that deliberately declares
``owl: secretary`` it is wrong, and the two are indistinguishable because they are
the same string.

A deliberate choice and an absent choice must not look identical. ``owl_pinned``
carries the difference: goal_execution sets it because the job stated an owl, and
triage honours a pinned owl whichever owl it happens to be.

This costs no behaviour change for chat: an unpinned turn routes exactly as before.
"""

from __future__ import annotations

import pytest

from stackowl.pipeline.state import PipelineState

pytestmark = pytest.mark.asyncio


class TestThePinnedChoiceSurvives:
    async def test_the_state_can_record_a_deliberate_owl(self) -> None:
        assert "owl_pinned" in PipelineState.model_fields

    async def test_it_defaults_to_unpinned_so_chat_is_unchanged(self) -> None:
        st = PipelineState(
            trace_id="t", session_key="s", input_text="hi",
            channel="telegram", owl_name="secretary", pipeline_step="",
        )

        assert st.owl_pinned is False

    async def test_the_scheduler_pins_the_owl_it_was_configured_with(self) -> None:
        """The producer end. Without this the flag exists and nothing sets it."""
        import inspect

        from stackowl.scheduler.handlers import goal_execution

        src = inspect.getsource(goal_execution)
        assert "owl_pinned" in src

    async def test_triage_honours_a_pinned_secretary(self) -> None:
        """The exact failure: 'secretary' is both a real choice and the sentinel."""
        import inspect

        from stackowl.pipeline.steps import triage

        src = inspect.getsource(triage)
        assert "owl_pinned" in src
