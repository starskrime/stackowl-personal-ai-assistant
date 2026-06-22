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
            return  # nothing was ever shown (fast turn) — leave the chat clean
        try:
            elapsed = int(round(self._clock() - self._started_at))
            footer = vocabulary.done_footer(elapsed, self._lang)
            await self._edit_status(self._chat_id, self._status_message_id, footer)
        except Exception as exc:  # noqa: BLE001
            log.telegram.warning(
                "[telegram] progress.settle: footer edit failed — continuing",
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
            return
        await self._edit_status(self._chat_id, self._status_message_id, self._decorated_text(now))
        self._last_edit_at = now

    async def _maybe_typing(self, now: float) -> None:
        if (
            self._last_typing_at is None
            or (now - self._last_typing_at) >= self._typing_reissue_interval_s
        ):
            await self._send_typing(self._chat_id)
            self._last_typing_at = now

    def _decorated_text(self, now: float) -> str:
        """Compose the displayed status: base phrase + (elapsed) once past threshold."""
        elapsed = int(now - self._started_at)
        # While still on the initial generic ACK (no real step arrived yet), switch
        # to a reassurance after the threshold.
        if self._progress_count <= 1 and elapsed >= self._reassure_after_s:
            base = vocabulary.render(ProgressKey.STILL_WORKING, self._lang)
        else:
            base = self._current_text or vocabulary.render(ProgressKey.ACK, self._lang)
        if elapsed >= self._elapsed_after_s:
            base = f"{base} {vocabulary.elapsed_suffix(elapsed, self._lang)}"
        return base
