"""TelegramProgressView — one mutating live-status message per turn.

A turn's progress chunks drive a SINGLE Telegram message that edits through
states ("Working on it…" → "Searching the web…" → "Writing your answer…"), backed
by a re-issued typing indicator. When the answer is delivered (as a separate,
clean message), the status collapses to a tiny "✓ done in 34s" footer.

A per-turn **background ticker** keeps the status alive during a long model
"thinking" gap with NO ReAct events (the common slow case — a big model taking
40-50s with zero tool calls): it sends the first "Working on it…" right after the
flicker guard, re-issues the typing indicator (Telegram clears it after ~5s), and
once past ``elapsed_after_s`` appends a ticking counter ("Working on it… (23s)"),
switching to a reassurance phrase after ``reassure_after_s``.

Design rules:
  * Edits are rate-limited (``edit_min_interval_s``) and coalesced — staying under
    Telegram's ~1 edit/sec cap.
  * The flicker guard suppresses the status message for the first
    ``flicker_guard_s`` so a fast turn never flashes a status that vanishes; the
    native typing indicator still fires immediately for liveness.
  * Every Bot API call is best-effort: a failed edit/typing/ticker must never
    block, delay, or corrupt the final answer (delivered independently).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable

from stackowl.infra.observability import log
from stackowl.pipeline.progress import vocabulary
from stackowl.pipeline.progress.vocabulary import ProgressKey

SendStatus = Callable[[int, str], Awaitable[int | None]]
EditStatus = Callable[[int, int, str], Awaitable[bool]]
SendTyping = Callable[[int], Awaitable[None]]
Clock = Callable[[], float]


class TelegramProgressView:
    """Per-turn live status state machine over a single Telegram message."""

    def __init__(
        self,
        *,
        chat_id: int,
        send_status: SendStatus,
        edit_status: EditStatus,
        send_typing: SendTyping,
        clock: Clock,
        lang: str = "en",
        edit_min_interval_s: float = 1.0,
        typing_reissue_interval_s: float = 4.0,
        flicker_guard_s: float = 0.4,
        tick_interval_s: float = 3.0,
        elapsed_after_s: float = 10.0,
        reassure_after_s: float = 30.0,
    ) -> None:
        self._chat_id = chat_id
        self._send_status = send_status
        self._edit_status = edit_status
        self._send_typing = send_typing
        self._clock = clock
        self._lang = lang
        self._edit_min_interval_s = edit_min_interval_s
        self._typing_reissue_interval_s = typing_reissue_interval_s
        self._flicker_guard_s = flicker_guard_s
        self._tick_interval_s = tick_interval_s
        self._elapsed_after_s = elapsed_after_s
        self._reassure_after_s = reassure_after_s

        self._started_at = clock()
        self._status_message_id: int | None = None
        self._answer_started = False
        self._last_edit_at: float | None = None
        self._last_typing_at: float | None = None
        self._current_text: str | None = None
        self._progress_count = 0
        self._ticker_task: asyncio.Task[None] | None = None
        # Counters behind the per-turn summary. They exist because the ANSWER to
        # "edit in place, or successive messages?" is a ratio (one message, N
        # edits) and the flood-control half of it is a total (every Bot API call
        # a turn spends). Neither is derivable from the failure-only WARNINGs
        # this module used to be observable through.
        self._edits_applied = 0
        self._edits_suppressed = 0
        self._typing_calls = 0

    # -- lifecycle ----------------------------------------------------------- #

    def start(self) -> None:
        """Launch the background liveness ticker (idempotent)."""
        if self._ticker_task is None:
            self._ticker_task = asyncio.create_task(self._ticker_loop())

    async def stop(self) -> None:
        """Cancel the ticker (idempotent; safe to call multiple times)."""
        task = self._ticker_task
        self._ticker_task = None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    # -- event hooks --------------------------------------------------------- #

    async def on_progress(self, text: str) -> None:
        """Record a new semantic state and render it (subject to the rate limit)."""
        if self._answer_started:
            return  # the answer supersedes progress; ignore late chunks
        try:
            self._current_text = text
            self._progress_count += 1
            now = self._clock()
            await self._maybe_typing(now)
            if self._status_message_id is None:
                # Send immediately only once the flicker guard has elapsed; before
                # that the ticker (first tick == guard) owns the initial send, so a
                # sub-guard turn never flashes a status.
                if now - self._started_at >= self._flicker_guard_s:
                    await self._send_first(now)
                return
            await self._maybe_edit(now)
        except Exception as exc:  # noqa: BLE001 — progress never breaks a turn
            log.telegram.warning(
                "[telegram] progress.on_progress: failed — continuing",
                exc_info=exc, extra={"_fields": {"chat_id": self._chat_id}},
            )

    def on_first_answer(self) -> None:
        """Stop touching the status message — the answer has begun streaming."""
        self._answer_started = True

    async def settle(self) -> None:
        """Stop the ticker and collapse the status into a '✓ done in Ns' footer."""
        await self.stop()
        if self._status_message_id is None:
            # Nothing was ever shown (fast turn) — leave the chat clean, but SAY
            # so: api_calls=0 is the flicker guard working, and reporting only
            # the branch that showed something would make the cheapest turns the
            # invisible ones.
            self._log_turn_summary("never_shown")
            return
        try:
            elapsed = int(round(self._clock() - self._started_at))
            footer = vocabulary.done_footer(elapsed, self._lang)
            await self._edit_status(self._chat_id, self._status_message_id, footer)
            self._edits_applied += 1
        except Exception as exc:  # noqa: BLE001
            log.telegram.warning(
                "[telegram] progress.settle: footer edit failed — continuing",
                exc_info=exc, extra={"_fields": {"chat_id": self._chat_id}},
            )
        self._log_turn_summary("settled")

    async def abort(self) -> None:
        """Stop the ticker and mark the status honestly FAILED.

        For a turn that raised or whose stream ended without an answer:
        settle()'s "✓ done" would be a lie, and doing nothing leaves the
        status stuck on "Still working on this… (Ns)" forever (confirmed
        production incident — an orphaned status ticked to 1670s in chat).
        """
        await self.stop()
        if self._status_message_id is None:
            self._log_turn_summary("never_shown")
            return  # nothing was ever shown — leave the chat clean
        try:
            elapsed = int(round(self._clock() - self._started_at))
            footer = vocabulary.abort_footer(elapsed, self._lang)
            await self._edit_status(self._chat_id, self._status_message_id, footer)
            self._edits_applied += 1
        except Exception as exc:  # noqa: BLE001
            log.telegram.warning(
                "[telegram] progress.abort: footer edit failed — continuing",
                exc_info=exc, extra={"_fields": {"chat_id": self._chat_id}},
            )
        self._log_turn_summary("aborted")

    def _log_turn_summary(self, outcome: str) -> None:
        """ONE INFO line per turn describing what the streaming path spent.

        THIS IS THE EVIDENCE FOR D12.4's ASK and it is deliberately at INFO:
        production runs at INFO, and the only prior trace of edit-in-place was
        four ERROR lines saying an edit FAILED. A claim whose sole evidence is
        its own failure branch is unanswerable by any volume of traffic.

        One line per TURN, not per edit — at ~1 edit/sec a 50s turn would emit
        fifty lines to state one ratio. `messages_sent` against `edits_applied`
        is the edit-in-place answer; `api_calls` is the flood-control answer.

        Best-effort like everything else here: a summary that could raise would
        turn an observability line into a way to lose a turn.
        """
        try:
            elapsed = int(round(self._clock() - self._started_at))
            messages_sent = 1 if self._status_message_id is not None else 0
            log.telegram.info(
                "[telegram] progress.turn: streaming summary",
                extra={
                    "_fields": {
                        "chat_id": self._chat_id,
                        "outcome": outcome,
                        "messages_sent": messages_sent,
                        "edits_applied": self._edits_applied,
                        "edits_suppressed": self._edits_suppressed,
                        "typing_calls": self._typing_calls,
                        "progress_events": self._progress_count,
                        "api_calls": messages_sent + self._edits_applied + self._typing_calls,
                        "elapsed_s": elapsed,
                    }
                },
            )
        except Exception as exc:  # noqa: BLE001 — observability never breaks a turn
            log.telegram.warning(
                "[telegram] progress.turn: summary failed — continuing",
                exc_info=exc, extra={"_fields": {"chat_id": self._chat_id}},
            )

    # -- ticker -------------------------------------------------------------- #

    async def _ticker_loop(self) -> None:
        """Wake periodically to keep the status alive across event-less gaps."""
        first = True
        try:
            while not self._answer_started:
                delay = self._flicker_guard_s if first else self._tick_interval_s
                first = False
                await asyncio.sleep(delay)
                if self._answer_started:
                    return
                await self._tick(self._clock())
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — a ticker fault must never escape
            log.telegram.warning(
                "[telegram] progress.ticker: failed — stopping ticker",
                exc_info=exc, extra={"_fields": {"chat_id": self._chat_id}},
            )

    async def _tick(self, now: float) -> None:
        if self._answer_started:
            return
        await self._maybe_typing(now)
        if self._status_message_id is None:
            if self._current_text is not None and now - self._started_at >= self._flicker_guard_s:
                await self._send_first(now)
            return
        await self._maybe_edit(now, force_due_to_elapsed=True)

    # -- helpers ------------------------------------------------------------- #

    async def _send_first(self, now: float) -> None:
        mid = await self._send_status(self._chat_id, self._decorated_text(now))
        if mid is not None:
            self._status_message_id = mid
            self._last_edit_at = now

    async def _maybe_edit(self, now: float, *, force_due_to_elapsed: bool = False) -> None:
        if self._status_message_id is None:
            return
        if self._last_edit_at is not None and (now - self._last_edit_at) < self._edit_min_interval_s:
            self._edits_suppressed += 1  # coalesced: this is what keeps us under the cap
            return
        await self._edit_status(self._chat_id, self._status_message_id, self._decorated_text(now))
        self._edits_applied += 1
        self._last_edit_at = now

    async def _maybe_typing(self, now: float) -> None:
        if (
            self._last_typing_at is None
            or (now - self._last_typing_at) >= self._typing_reissue_interval_s
        ):
            await self._send_typing(self._chat_id)
            self._typing_calls += 1
            self._last_typing_at = now

    def _decorated_text(self, now: float) -> str:
        """Compose the displayed status: base phrase + (elapsed) once past threshold."""
        elapsed = int(now - self._started_at)
        # While still on the initial generic ACK (no real step arrived yet), switch
        # to a reassurance after the threshold.
        if self._progress_count <= 1 and elapsed >= self._reassure_after_s:
            base = vocabulary.render(ProgressKey.STILL_WORKING, self._lang)
        else:
            # seed=self._started_at: one spell per TURN. Without a seed this
            # fallback re-draws on every ticker edit and the word visibly changes
            # while the user waits, which reads as a glitch rather than a flourish.
            base = self._current_text or vocabulary.render(
                ProgressKey.ACK, self._lang, seed=self._started_at,
            )
        if elapsed >= self._elapsed_after_s:
            base = f"{base} {vocabulary.elapsed_suffix(elapsed, self._lang)}"
        return base
