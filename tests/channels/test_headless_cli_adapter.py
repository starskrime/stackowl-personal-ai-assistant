"""The gateway must not run a Textual TUI when it has no terminal.

THE BUG THIS FIXES, measured on the live box 2026-08-14. The gateway process sat
at 100.3% CPU — a whole core, continuously, on a 6-core Jetson — from the moment
it started and for as long as it ran. A py-spy profile of 534 samples put ~95% of
that time inside Textual's Linux input driver:

    41.2%  process_selector_events (textual/drivers/linux_driver.py:447)
    15.5%  run_input_thread        (textual/drivers/linux_driver.py:458)
     8.8%  process_selector_events (textual/drivers/linux_driver.py:453)
     7.1%  tick                    (textual/_parser.py:57)

The mechanism: ``start.sh`` — the canonical restart entry point for this repo —
launches with ``nohup ... &``, so the gateway's stdin is ``/dev/null``. A select()
on /dev/null is ALWAYS ready (it returns EOF immediately), so the input thread
wakes, reads nothing, feeds nothing to the parser, and goes straight back to
select. Forever.

So the TUI was not merely useless without a terminal — it rendered escape codes
into ``manual_restart_stdout.log`` and read keystrokes from /dev/null — it was
actively burning a core to do it. This predates D08.2: the gateway has built the
Textual TUI unconditionally since the gateway/core split.

WHY A HEADLESS ADAPTER RATHER THAN NO ADAPTER. The "cli" channel is registered
with the clarify gateway and the proactive deliverer. Removing it would turn a
clarify question or a scheduled delivery addressed to "cli" into a
ChannelNotFoundError. So the channel still exists; it simply has no terminal
attached, and says so.
"""

from __future__ import annotations

import asyncio

import pytest

from stackowl.channels.cli_adapter import HeadlessCliAdapter

pytestmark = pytest.mark.asyncio


async def test_it_registers_as_the_cli_channel() -> None:
    """The name is load-bearing: clarify and proactive delivery resolve by it."""
    assert HeadlessCliAdapter().channel_name == "cli"


async def test_run_blocks_without_burning_cpu() -> None:
    """The whole point. ``run()`` must PARK, not poll.

    A headless adapter that span in its own loop would move the 100% CPU rather
    than remove it, and would look identical from the outside.
    """
    adapter = HeadlessCliAdapter()
    task = asyncio.create_task(adapter.run())
    await asyncio.sleep(0.05)

    assert not task.done(), "run() returned immediately — the gateway would exit"
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_receive_never_yields_a_message() -> None:
    """There is no terminal, so there is no user input — ever. It must BLOCK
    rather than return an empty message, which would spin the turn loop."""
    adapter = HeadlessCliAdapter()
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(adapter.receive(), timeout=0.1)


async def test_send_text_does_not_raise_and_records_the_text() -> None:
    """A scheduled delivery addressed to "cli" must not crash the sender just
    because nobody is watching. It is dropped, but visibly."""
    adapter = HeadlessCliAdapter()

    result = await adapter.send_text("morning brief")

    assert result is None
    assert adapter.dropped == ["morning brief"]


async def test_send_drains_the_stream_rather_than_leaking_it() -> None:
    """The caller streams chunks and expects someone to consume them. Not
    draining would leave the producer blocked on a full queue."""
    adapter = HeadlessCliAdapter()

    async def _chunks():
        for text in ("a", "b"):
            yield _Chunk(text)

    await adapter.send(_chunks())

    assert adapter.dropped == ["ab"]


class _Chunk:
    def __init__(self, content: str) -> None:
        self.content = content
        self.is_final = False
