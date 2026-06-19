"""Tests for the no-progress honest-floor trigger (Task 4 — Turn Progress Supervisor).

Covers the G2 honesty gap: a turn that made NO forward progress (circuit-breaker bounced
a tool repeatedly) and delivered nothing real must ship an honest floor instead of the
model's dressed-up draft.

INDEPENDENT of the consequential ledger — this path fires when is_consequential_giveup_now
returns False but is_no_progress_giveup returns True.
"""

from __future__ import annotations

from stackowl.pipeline.giveup_floor import (
    is_consequential_giveup_now,
    is_no_progress_giveup,
    surface_consequential_giveup_floor,
)
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import ResponseChunk


def _state(**kw: object) -> PipelineState:
    base: dict[str, object] = dict(
        trace_id="t",
        session_id="s",
        input_text="make me a chart",
        channel="cli",
        owl_name="o",
        pipeline_step="execute",
    )
    base.update(kw)
    return PipelineState(**base)  # type: ignore[arg-type]


def _draft(content: str, is_floor: bool = False) -> ResponseChunk:
    return ResponseChunk(
        content=content,
        is_final=False,
        chunk_index=0,
        trace_id="t",
        owl_name="o",
        is_floor=is_floor,
    )


async def _run_floor(state: PipelineState) -> PipelineState:
    """Drive the async surface_consequential_giveup_floor."""
    return await surface_consequential_giveup_floor(state)


# ---------------------------------------------------------------------------
# is_no_progress_giveup predicate tests
# ---------------------------------------------------------------------------


def test_no_progress_predicate_true_when_spiraled_and_delivered_nothing() -> None:
    s = _state(
        responses=(_draft("All done — your chart is ready!"),),
        turn_made_progress=False,
        no_progress_tools=("execute_code",),
        delivered_successes=(),
    )
    assert is_no_progress_giveup(s) is True


def test_no_progress_predicate_false_when_progressing() -> None:
    s = _state(
        responses=(_draft("Here is your answer."),),
        turn_made_progress=True,
        no_progress_tools=(),
    )
    assert is_no_progress_giveup(s) is False


def test_no_progress_predicate_false_when_no_tools_bounced() -> None:
    """turn_made_progress=False but no_progress_tools is empty — not a spiral."""
    s = _state(
        responses=(_draft("draft"),),
        turn_made_progress=False,
        no_progress_tools=(),
    )
    assert is_no_progress_giveup(s) is False


def test_no_progress_predicate_false_when_delivered_something() -> None:
    """Tool bounced but a separate success was delivered OUT — not a give-up."""
    s = _state(
        responses=(_draft("Your file was sent!"),),
        turn_made_progress=False,
        no_progress_tools=("execute_code",),
        delivered_successes=("send_file",),
    )
    assert is_no_progress_giveup(s) is False


def test_no_progress_predicate_false_when_already_floored() -> None:
    """Don't double-floor: if responses already carry is_floor=True, no-op."""
    existing_floor = _draft("Sorry, I was unable to complete that.", is_floor=True)
    s = _state(
        responses=(existing_floor,),
        turn_made_progress=False,
        no_progress_tools=("execute_code",),
        delivered_successes=(),
    )
    assert is_no_progress_giveup(s) is False


# ---------------------------------------------------------------------------
# surface_consequential_giveup_floor integration tests
# ---------------------------------------------------------------------------


async def test_no_progress_turn_delivering_nothing_floors() -> None:
    """Tool spiraled (no progress), bounced, nothing delivered, draft is confident non-floor."""
    draft = _draft("All done — your chart is ready!")
    s = _state(
        responses=(draft,),
        turn_made_progress=False,
        no_progress_tools=("execute_code",),
        delivered_successes=(),
    )
    assert is_no_progress_giveup(s) is True
    out = await _run_floor(s)
    delivered = "".join(c.content for c in out.responses)
    assert "your chart is ready" not in delivered, (
        f"OVERCLAIM SHIPPED: dressed-up partial reached user. delivered={delivered!r}"
    )
    assert any(getattr(c, "is_floor", False) for c in out.responses), (
        f"expected an is_floor=True honest floor chunk; got {out.responses!r}"
    )
    assert delivered.strip(), "floor must be non-empty"


async def test_progressing_turn_not_floored() -> None:
    """turn_made_progress=True → no_progress path is NOT triggered."""
    draft = _draft("Here is your answer.")
    s = _state(
        responses=(draft,),
        turn_made_progress=True,
        no_progress_tools=(),
    )
    assert is_no_progress_giveup(s) is False
    out = await _run_floor(s)
    delivered = "".join(c.content for c in out.responses)
    assert "Here is your answer." in delivered, (
        f"a progressing turn was incorrectly floored. delivered={delivered!r}"
    )
    assert not any(getattr(c, "is_floor", False) for c in out.responses)


async def test_conversational_zero_tool_turn_not_floored() -> None:
    """Default turn_made_progress=True (never entered tracker) — never floored."""
    draft = _draft("Hi there!")
    s = _state(responses=(draft,))  # defaults: turn_made_progress=True, no_progress_tools=()
    assert is_no_progress_giveup(s) is False
    out = await _run_floor(s)
    delivered = "".join(c.content for c in out.responses)
    assert "Hi there!" in delivered
    assert not any(getattr(c, "is_floor", False) for c in out.responses)


async def test_no_progress_turn_with_delivery_not_floored() -> None:
    """A no-progress-tool bounce but something WAS delivered OUT — not a give-up."""
    draft = _draft("Your file was sent!")
    s = _state(
        responses=(draft,),
        turn_made_progress=False,
        no_progress_tools=("execute_code",),
        delivered_successes=("send_file",),
    )
    out = await _run_floor(s)
    delivered = "".join(c.content for c in out.responses)
    assert "Your file was sent!" in delivered
    assert not any(getattr(c, "is_floor", False) for c in out.responses)


async def test_already_floored_response_not_double_processed() -> None:
    """Existing is_floor=True response must not be replaced by another floor."""
    existing_floor = _draft("Sorry, I was unable to complete that.", is_floor=True)
    s = _state(
        responses=(existing_floor,),
        turn_made_progress=False,
        no_progress_tools=("execute_code",),
        delivered_successes=(),
    )
    out = await _run_floor(s)
    # Responses unchanged — same object tuple
    assert out.responses == (existing_floor,)


# ---------------------------------------------------------------------------
# REGRESSION GUARD — consequential-giveup path must still floor as before.
# ---------------------------------------------------------------------------


async def test_consequential_and_no_progress_both_true_takes_consequential_path(monkeypatch) -> None:
    """When BOTH consequential-giveup and no-progress-giveup would independently fire,
    the consequential path wins (it is checked FIRST in surface_consequential_giveup_floor).
    The floor is produced and the no-progress path is never reached.
    """
    draft = _draft("I've sent the file and ran your code — all done!")
    s = _state(
        responses=(draft,),
        # Consequential failure shape — budget-capped so is_consequential_giveup_now fires
        consequential_snapshot_taken=True,
        consequential_failures=("send_file",),
        consequential_successes=(),
        delivered_successes=(),
        recovered_consequential=(),
        budget_capped=True,
        # No-progress shape — would also independently fire
        turn_made_progress=False,
        no_progress_tools=("execute_code",),
    )

    # Both predicates must fire on the input state
    assert is_consequential_giveup_now(s) is True, "consequential predicate must fire"
    assert is_no_progress_giveup(s) is True, "no-progress predicate must also fire"

    # Spy on synthesize_floor to capture which failed_capability was passed.
    # This proves which branch ran: consequential passes "send_file", no-progress passes "execute_code".
    # Patch it at the point of import in giveup_floor (where it's actually called).
    captured = {}
    from stackowl.pipeline import giveup_floor as gf_module
    real_synthesize_floor = gf_module.synthesize_floor

    def spy_synthesize_floor(*args, **kwargs):
        captured["failed_capability"] = kwargs.get("failed_capability")
        return real_synthesize_floor(*args, **kwargs)

    monkeypatch.setattr(gf_module, "synthesize_floor", spy_synthesize_floor)

    out = await _run_floor(s)
    delivered = "".join(c.content for c in out.responses)

    # An is_floor chunk must be produced
    assert any(getattr(c, "is_floor", False) for c in out.responses), (
        f"expected an is_floor chunk; got {out.responses!r}"
    )
    assert delivered.strip(), "floor must be non-empty"

    # ROBUST PROOF: The consequential branch passed "send_file" to synthesize_floor.
    # If the no-progress branch had run, it would have passed "execute_code".
    assert captured.get("failed_capability") == "send_file", (
        f"expected consequential branch to pass failed_capability='send_file', "
        f"but got {captured.get('failed_capability')!r} — the no-progress branch ran instead"
    )

    # Sanity check: after flooring, is_no_progress_giveup on the OUTPUT
    # state returns False (the existing is_floor response blocks the double-floor
    # guard, proving the first branch completed and the second would have been no-op'd).
    assert is_no_progress_giveup(out) is False, (
        "output state with is_floor=True must not re-trigger the no-progress predicate"
    )


async def test_consequential_giveup_still_floors() -> None:
    """Regression: a turn with consequential failures + snapshot STILL floors via
    the consequential path (the _floor_chunk refactor must be byte-identical)."""
    draft = _draft("I've sent you the file — enjoy!")
    s = _state(
        responses=(draft,),
        # Consequential snapshot fields (mirrors the budget-cap journey shape)
        consequential_snapshot_taken=True,
        consequential_failures=("send_file",),
        consequential_successes=(),
        delivered_successes=(),
        recovered_consequential=(),
        budget_capped=True,
        # turn_made_progress=True (default) so no-progress path is not taken
        turn_made_progress=True,
        no_progress_tools=(),
    )
    out = await _run_floor(s)
    delivered = "".join(c.content for c in out.responses)
    assert "enjoy" not in delivered, (
        f"consequential floor regression: overclaim shipped. delivered={delivered!r}"
    )
    assert any(getattr(c, "is_floor", False) for c in out.responses), (
        f"consequential floor regression: no is_floor chunk. responses={out.responses!r}"
    )
    assert delivered.strip(), "consequential floor must be non-empty"
