"""A recovered turn must resume with the owl that ran it, not be routed again.

WHAT HE SAW, 2026-09-03. He asked: "Create a agent which will check job market
near me... zip code 75025... senior lead engineer and up... minimum 200k... 1
times only per day at 2 pm." The reply he got was "Good news: that agent already
exists — I'm it." He read that as the platform saying "done" without doing
anything and without asking.

WHAT ACTUALLY HAPPENED, from the logs and the database:

    04:28:06  routed to secretary
    04:29:52  owl_build.execute -> persist_owl: stored  (owls.jobmarket CREATED)
    04:30:14  owl_build.execute -> authority WIDENED
    04:30:20  persist_turn: FLOORED TURN — the answer never reached him
    04:31:11  the loop recovers it as recover-6ba15e00ac1c
    04:31:13  [router] selected JOBMARKET  <-- the owl the turn had just built
    04:31:23  owls_list -> "that agent already exists — I'm it"

The work was done correctly: `owls.jobmarket` exists, created 04:29:52.445, role
"Senior/Lead/Staff/Principal+ IC engineering, zip 75025, $200K+, once per day at
2 PM", scheduled daily@14:00. Only the ANSWER was wrong.

THE CAUSE. ``RetryActuator`` built its synthetic state with a hardcoded
``owl_name="secretary"`` — which is exactly ``triage._FALLBACK_OWL``, so triage
read it as "nobody chose an owl" and routed the goal afresh. By then the owl the
turn had CREATED matched the words in the goal, so it captured its own recovery
and reported the world as it now found it.

WHAT ELSE THE SAME CAUSE REACHES: every turn that creates or renames anything
routable and then floors. The recovery is routed against a world the turn itself
changed, so the more successful the turn was, the more likely its recovery is
handed to the wrong owl.
"""

from __future__ import annotations

from types import SimpleNamespace

from stackowl.pipeline.durable.task_loop_runner import actuator_row_for
from stackowl.pipeline.steps.triage import _FALLBACK_OWL


def _task(owl: str = "secretary"):
    return SimpleNamespace(
        task_id="6ba15e00", session_key="owl:secretary:telegram:dm:72055773",
        goal="Create a agent which will check job market near me",
        banned_capabilities=[], attempt_count=1, last_error="floored",
        channel="telegram", destination="telegram:72055773",
        owl_name=owl, status="failed", created_at="2026-09-03T04:28:05+00:00",
    )


def test_the_row_carries_the_owl_that_RAN_the_turn() -> None:
    assert actuator_row_for(_task("secretary")).owl_name == "secretary"
    assert actuator_row_for(_task("scout")).owl_name == "scout"


def test_the_retry_PINS_that_owl_so_triage_does_not_re_route() -> None:
    """The exact defect. `owl_name="secretary"` alone is indistinguishable from
    the fallback, so triage re-decides — and the world the turn changed gets to
    choose who explains the change."""
    import inspect

    from stackowl.pipeline import retry_actuator

    src = inspect.getsource(retry_actuator)
    assert 'owl_name="secretary"' not in src, (
        "the retry still hardcodes the fallback owl, which triage reads as an "
        "absent choice and routes around"
    )
    assert "owl_pinned=bool(row.owl_name)" in src


def test_pinning_is_what_makes_the_owl_STICK() -> None:
    """Documents the contract this depends on: triage skips routing when the owl
    was pinned OR is not the fallback. Naming secretary without pinning satisfies
    NEITHER, which is why the original bug was invisible — the field was set."""
    import inspect

    from stackowl.pipeline.steps import triage

    src = inspect.getsource(triage)
    assert "if state.owl_pinned or state.owl_name != _FALLBACK_OWL:" in src
    assert _FALLBACK_OWL == "secretary", (
        "the fallback owl changed; the retry's default now collides with a "
        "different name and this whole test is about the wrong one"
    )


def test_a_task_with_NO_owl_still_retries() -> None:
    """The control. Pinning must not become a requirement — a task recovered
    without an owl (an older row, a scheduler lane) has to keep working, falling
    back to secretary UNPINNED so triage can route it properly."""
    row = actuator_row_for(_task(""))

    assert row.owl_name == ""
    assert row.goal.startswith("Create a agent")
