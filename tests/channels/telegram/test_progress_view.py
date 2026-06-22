"""TelegramProgressView — flicker guard, rate-limited edits, typing, footer."""

from __future__ import annotations

import pytest

from stackowl.channels.telegram.progress_render import TelegramProgressView


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


class _Recorder:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []
        self.edits: list[tuple[int, int, str]] = []
        self.typing: list[int] = []
        self._next_id = 100

    async def send_status(self, chat_id: int, text: str) -> int | None:
        self.sent.append((chat_id, text))
        self._next_id += 1
        return self._next_id

    async def edit_status(self, chat_id: int, message_id: int, text: str) -> bool:
        self.edits.append((chat_id, message_id, text))
        return True

    async def send_typing(self, chat_id: int) -> None:
        self.typing.append(chat_id)


def _view(rec: _Recorder, clock: _Clock, **over: float) -> TelegramProgressView:
    params: dict[str, float] = dict(
        edit_min_interval_s=1.0, typing_reissue_interval_s=4.0, flicker_guard_s=0.4
    )
    params.update(over)
    return TelegramProgressView(
        chat_id=42,
        send_status=rec.send_status,
        edit_status=rec.edit_status,
        send_typing=rec.send_typing,
        clock=clock,
        lang="en",
        **params,
    )


@pytest.mark.asyncio
async def test_flicker_guard_suppresses_status_for_fast_turn() -> None:
    rec, clock = _Recorder(), _Clock()
    view = _view(rec, clock)
    # All progress within the 0.4s guard, then the answer arrives.
    await view.on_progress("⏳ Working on it…")
    clock.t = 0.1
    await view.on_progress("🔎 Searching…")
    view.on_first_answer()
    await view.settle()
    # No status message ever sent → no footer → clean transcript.
    assert rec.sent == []
    assert rec.edits == []
    # Typing still fired immediately for liveness.
    assert rec.typing == [42]


@pytest.mark.asyncio
async def test_status_sent_after_guard_then_rate_limited_edit() -> None:
    rec, clock = _Recorder(), _Clock()
    view = _view(rec, clock)
    await view.on_progress("⏳ Working on it…")  # t=0, held by guard
    assert rec.sent == []
    clock.t = 1.0
    await view.on_progress("🔎 Searching the web…")  # guard passed → status sent
    assert len(rec.sent) == 1
    assert rec.sent[0] == (42, "🔎 Searching the web…")
    # A second update <1s after the send is coalesced (no edit yet).
    clock.t = 1.2
    await view.on_progress("📂 Reading 3 files…")
    assert rec.edits == []
    # Past the edit interval → the latest pending text flushes.
    clock.t = 2.5
    await view.on_progress("✍️ Writing your answer…")
    assert rec.edits == [(42, 101, "✍️ Writing your answer…")]


@pytest.mark.asyncio
async def test_settle_collapses_to_done_footer() -> None:
    rec, clock = _Recorder(), _Clock()
    view = _view(rec, clock)
    clock.t = 1.0
    await view.on_progress("🔎 Searching…")  # status sent (id 101)
    view.on_first_answer()
    clock.t = 34.0
    await view.settle()  # elapsed from construction (t=0) to settle (t=34)
    assert rec.edits[-1] == (42, 101, "✓ done in 34s")


@pytest.mark.asyncio
async def test_typing_reissued_after_interval() -> None:
    rec, clock = _Recorder(), _Clock()
    view = _view(rec, clock)
    await view.on_progress("a")  # typing at t=0
    clock.t = 2.0
    await view.on_progress("b")  # < 4s → no re-issue
    clock.t = 5.0
    await view.on_progress("c")  # >= 4s since last typing → re-issued
    assert rec.typing == [42, 42]


@pytest.mark.asyncio
async def test_progress_ignored_after_answer_started() -> None:
    rec, clock = _Recorder(), _Clock()
    view = _view(rec, clock, flicker_guard_s=0.0)
    clock.t = 0.0
    await view.on_progress("🔎 Searching…")  # status sent
    view.on_first_answer()
    clock.t = 2.0
    await view.on_progress("late progress that must be ignored")
    # No edit from the post-answer progress.
    assert all("late progress" not in t for (_c, _m, t) in rec.edits)


@pytest.mark.asyncio
async def test_ticker_sends_held_ack_after_guard_no_other_events() -> None:
    # The swallowed-ACK bug: a 0-tool turn emits only the ACK, then nothing for a
    # long model think. The ticker MUST surface "Working on it…" on its own.
    rec, clock = _Recorder(), _Clock()
    view = _view(rec, clock)
    await view.on_progress("⏳ Working on it…")  # t=0, held by guard, no send yet
    assert rec.sent == []
    clock.t = 0.4  # guard elapsed
    await view._tick(clock.t)  # ticker's first tick
    assert len(rec.sent) == 1
    assert "Working on it" in rec.sent[0][1]


@pytest.mark.asyncio
async def test_ticker_appends_elapsed_and_reassurance() -> None:
    rec, clock = _Recorder(), _Clock()
    view = _view(rec, clock, elapsed_after_s=10.0, reassure_after_s=30.0)
    await view.on_progress("⏳ Working on it…")
    clock.t = 0.4
    await view._tick(clock.t)  # status sent, no elapsed yet
    assert "(0s)" not in rec.sent[0][1]
    # Past elapsed threshold → counter appears.
    clock.t = 12.0
    await view._tick(clock.t)
    assert any("(12s)" in t for (_c, _m, t) in rec.edits)
    # Past reassurance threshold, still on the initial ACK → swaps phrase.
    clock.t = 34.0
    await view._tick(clock.t)
    assert any("Still working on this" in t and "(34s)" in t for (_c, _m, t) in rec.edits)


@pytest.mark.asyncio
async def test_ticker_keeps_real_step_text_with_elapsed() -> None:
    # When a real step (count>1) is current, the elapsed counter rides on THAT
    # text, not the reassurance phrase.
    rec, clock = _Recorder(), _Clock()
    view = _view(rec, clock)
    await view.on_progress("⏳ Working on it…")
    clock.t = 1.0
    await view.on_progress("🔎 Searching the web…")  # count=2, status sent
    clock.t = 40.0
    await view._tick(clock.t)
    assert any("Searching the web" in t and "(40s)" in t for (_c, _m, t) in rec.edits)


@pytest.mark.asyncio
async def test_ticker_lifecycle_starts_and_stops_cleanly() -> None:
    import asyncio

    rec, clock = _Recorder(), _Clock()
    view = _view(rec, clock, flicker_guard_s=0.0, tick_interval_s=0.01)
    view.start()
    view.start()  # idempotent — must not spawn a second task
    await view.on_progress("⏳ Working on it…")
    await asyncio.sleep(0.05)  # let a couple of real ticks fire
    assert rec.sent, "ticker should have surfaced a status"
    view.on_first_answer()
    await view.stop()
    await view.stop()  # idempotent
    edits_after_stop = len(rec.edits)
    await asyncio.sleep(0.03)
    assert len(rec.edits) == edits_after_stop  # ticker truly stopped


@pytest.mark.asyncio
async def test_send_status_failure_does_not_raise() -> None:
    rec, clock = _Recorder(), _Clock()

    async def failing_send(chat_id: int, text: str) -> int | None:
        return None  # simulate a Bot API miss

    view = TelegramProgressView(
        chat_id=42, send_status=failing_send, edit_status=rec.edit_status,
        send_typing=rec.send_typing, clock=clock, flicker_guard_s=0.0,
    )
    await view.on_progress("x")  # no message_id captured
    await view.on_progress("y")  # must not try to edit a missing message
    await view.settle()          # no status → no footer
    assert rec.edits == []
