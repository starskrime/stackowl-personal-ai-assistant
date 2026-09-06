"""The edit-in-place path must leave INFO evidence of what it actually did.

WHY THIS EXISTS — D12.4's Ask: *"Do we edit-in-place, or post successive
messages? (Affects flood-control, which has bitten us.)"*

**The code answers it and production could not.** `TelegramProgressView` is a
complete edit-in-place implementation — one status message per turn, mutated
through its states, rate-limited under Telegram's ~1 edit/sec cap — and it is
wired at `adapter.py:513`. But MEASURED 2026-09-06 across the full retained log
window (10 files, 555,598 records, 1,159 of them from the telegram module):

  * `adapter.send_status` — the call that creates the one mutating message —
    appears **zero times**, because its success path logs nothing at all;
  * every line `progress_render.py` emits is a WARNING on a failure branch;
  * the ONLY production trace of edit-in-place is **4 ERROR lines**,
    `"adapter.edit_message: edit failed — fail open"`.

So the sole evidence that the streaming path existed was evidence of it
FAILING. D12.4's closing check says this in its own words: bounding the grep
reported CLOSEABLE on those four hits, and "counting a FAILURE as evidence that
in-place editing works is the denominator error twice in one day."

That is the D08.1 defect — a claim whose only evidence line is below INFO — and
this programme has now paid for it three times. It is also why the item sat at
six stages with an empty record: **no volume of traffic could ever have answered
its Ask**, so there was nothing honest to write down.

WHAT THE LINE HAS TO CARRY, and why one line per turn rather than per edit. At
~1 edit/sec a 50s turn would emit fifty lines to say one thing. The question is
a RATIO — one message against N edits — and the flood-control half of the Ask is
a TOTAL: how many Bot API calls the streaming path spends on a turn. Both are
per-turn quantities, so the summary belongs at teardown, where `settle()` and
`abort()` already converge.

A turn that shows nothing is evidence too: it is the flicker guard working, and
`api_calls=0` is the number that proves it. So the line is emitted even when no
status was ever sent, which is the one case the old early-`return` skipped.
"""

from __future__ import annotations

import logging

import pytest

from stackowl.channels.telegram.progress_render import TelegramProgressView

from .test_progress_view import _Clock, _Recorder, _view

pytestmark = pytest.mark.asyncio

_SUMMARY = "[telegram] progress.turn: streaming summary"


def _summary(caplog: pytest.LogCaptureFixture) -> dict[str, object]:
    """The one summary record's fields, asserting there is exactly one."""
    recs = [r for r in caplog.records if r.getMessage() == _SUMMARY]
    assert len(recs) == 1, f"expected exactly one summary line, got {len(recs)}"
    assert recs[0].levelno >= logging.INFO, (
        "the summary is the evidence for D12.4's Ask; production runs at INFO, so "
        "a DEBUG line here could never answer it"
    )
    return dict(getattr(recs[0], "_fields", {}))


async def test_a_streamed_turn_reports_one_message_and_many_edits(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """THE ASK, ANSWERED AS A RATIO. Edit-in-place means the message count stays
    at one while the edit count climbs; successive-message posting would show the
    opposite. The line has to make those two distinguishable."""
    rec, clock = _Recorder(), _Clock()
    view = _view(rec, clock)

    clock.t = 0.5  # past the flicker guard, so the first progress event sends
    await view.on_progress("⏳ Working on it…")
    for i in range(1, 5):
        clock.t = 0.5 + i  # one second apart — each clears the edit rate limit
        await view.on_progress(f"🔎 Step {i}…")
    view.on_first_answer()
    with caplog.at_level(logging.INFO):
        await view.settle()

    fields = _summary(caplog)
    assert fields["messages_sent"] == 1, "edit-in-place posts exactly ONE message"
    assert fields["edits_applied"] == 5, fields  # 4 progress edits + the done footer
    assert fields["outcome"] == "settled"
    assert rec.sent == [(42, rec.sent[0][1])] and len(rec.edits) == 5


async def test_the_rate_limit_is_visible_as_suppressed_edits(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The flood-control half of the Ask. Coalesced edits are the mechanism that
    keeps us under Telegram's cap, and a number nobody can see is a mechanism
    nobody can check."""
    rec, clock = _Recorder(), _Clock()
    view = _view(rec, clock)

    clock.t = 0.5
    await view.on_progress("⏳ Working on it…")
    for i in range(6):  # six events inside one rate-limit window
        clock.t = 0.5 + (i + 1) * 0.1
        await view.on_progress(f"🔎 Step {i}…")
    view.on_first_answer()
    with caplog.at_level(logging.INFO):
        await view.settle()

    fields = _summary(caplog)
    assert fields["edits_suppressed"] == 6, fields
    assert fields["edits_applied"] == 1, fields  # only the footer got through
    assert fields["progress_events"] == 7, fields


async def test_a_fast_turn_reports_that_it_spent_nothing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """THE CASE THE EARLY RETURN SKIPPED. A turn inside the flicker guard shows
    no status at all — and that silence is the guard WORKING. Logged only on the
    branch that showed something, `api_calls=0` could never be observed, and the
    cheapest turns would be the invisible ones."""
    rec, clock = _Recorder(), _Clock()
    view = _view(rec, clock)

    await view.on_progress("⏳ Working on it…")  # t=0, inside the 0.4s guard
    view.on_first_answer()
    with caplog.at_level(logging.INFO):
        await view.settle()

    fields = _summary(caplog)
    assert fields["outcome"] == "never_shown"
    assert fields["messages_sent"] == 0 and fields["edits_applied"] == 0
    assert rec.sent == [] and rec.edits == []


async def test_an_aborted_turn_is_not_reported_as_settled(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`abort()` exists because a stuck status ticked to 1670s in production. The
    summary must tell the two endings apart, or that incident is invisible again."""
    rec, clock = _Recorder(), _Clock()
    view = _view(rec, clock)

    clock.t = 0.5
    await view.on_progress("⏳ Working on it…")
    clock.t = 9.0
    with caplog.at_level(logging.INFO):
        await view.abort()

    fields = _summary(caplog)
    assert fields["outcome"] == "aborted"
    assert fields["elapsed_s"] == 9


async def test_api_calls_totals_every_bot_call_the_stream_made(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """THE FLOOD-CONTROL NUMBER. The Ask names flood control specifically, and the
    quantity that governs it is the TOTAL outbound calls a turn spends — sends,
    edits and typing indicators alike. Counting only edits would understate it by
    the typing re-issues, which are themselves Bot API calls."""
    rec, clock = _Recorder(), _Clock()
    view = _view(rec, clock)

    clock.t = 0.5
    await view.on_progress("⏳ Working on it…")
    clock.t = 5.0  # past the 4s typing re-issue interval
    await view.on_progress("🔎 Searching…")
    view.on_first_answer()
    with caplog.at_level(logging.INFO):
        await view.settle()

    fields = _summary(caplog)
    assert fields["api_calls"] == len(rec.sent) + len(rec.edits) + len(rec.typing), (
        f"api_calls must equal every recorded Bot API call: {fields}"
    )
    assert fields["typing_calls"] == len(rec.typing)


async def test_the_summary_never_breaks_a_turn(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """VACUITY'S OPPOSITE, and the house rule: progress is best-effort. A summary
    that could raise would turn an observability line into a way to lose a turn."""
    rec, clock = _Recorder(), _Clock()
    view = _view(rec, clock)
    clock.t = 0.5
    await view.on_progress("⏳ Working on it…")

    class _Boom:
        def __call__(self, *a: object, **k: object) -> float:
            raise RuntimeError("clock exploded")

    view._clock = _Boom()  # type: ignore[assignment]
    with caplog.at_level(logging.INFO):
        await view.settle()  # must not raise

    assert isinstance(view, TelegramProgressView)


class TestBothHalvesOfTheAskAreAnswerableAtINFO:
    """D12.4 asks ONE question with two measurable halves, and the SAME cause hid
    both: the status path's only lines were failure WARNINGs, and the answer
    path's split line was DEBUG. Production runs at INFO, so both read zero while
    both were happening.

    `part_count` is the successive-messages half — a long answer over Telegram's
    4096-char cap becomes N messages, and N is what flood control spends. Guarded
    here rather than left to a comment because a level is one token to change and
    nothing else would notice.
    """

    def test_the_answer_split_line_is_INFO(self) -> None:
        import ast
        from pathlib import Path

        src = Path("src/stackowl/channels/telegram/adapter.py")
        tree = ast.parse(src.read_text(encoding="utf-8"))
        levels = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "[telegram] adapter.send_text: decision split"
        }

        assert levels, "the answer-split line has gone — the Ask lost its other half"
        assert levels <= {"info", "warning", "error"}, (
            f"the split count is evidence for D12.4 and production runs at INFO: {levels}"
        )
