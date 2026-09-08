"""A recovered answer goes to the task's OWN addressee, or to nobody — never to
whichever chat happened to speak last.

MEASURED 2026-09-08 across every retained log, and the defect has two faces that
are one cause.

``retry_actuator._deliver_success`` picks its branch on ``row.channel_chat_id``,
which ``task_loop_runner`` derives from the task's ``destination`` via
``_chat_id_of``. A destination of ``rca``, or a bare ``telegram``, or NULL yields
no chat id, so the call falls through to the untargeted form::

    await adapter.send_text(answer_text)      # no chat_id

Telegram then resolves that against ``self._last_chat_id`` — process-global state
holding whichever chat most recently sent an inbound update. So the outcome of a
recovered internal task depended entirely on whether somebody had recently
messaged the bot:

* ``_last_chat_id`` empty  -> the answer was DISCARDED. 60 occurrences of
  ``[telegram] adapter.send_text: no active chat (best-effort) — message
  dropped``, and 60 of 60 were immediately preceded by
  ``retry_actuator.attempt_retry: entry`` (denominator: 241 attempt_retry
  entries in the same window). ``send_text`` signals that drop by RETURNING
  ``None`` — and every other adapter (cli, socket, discord, whatsapp) returns
  ``None`` on SUCCESS, so no caller can read it. ``_deliver_success`` returned
  normally, ``attempt_retry`` reported ``completed``, and the loop stamped
  ``delivered_at``.
* ``_last_chat_id`` set    -> the answer was SENT TO THAT CHAT. Three re-drives
  of tasks whose ``destination`` was NULL — tasks that owe no delivery and name
  no addressee — reached a real chat this way, matched by pairing each
  ``[loop] re-driving a recovered task through the retry actuator`` with the
  next telegram send outcome within 60s.

``channels/base.py`` already forbids exactly this, in its own words: *"A
'fallback chat' is NEVER fabricated (that re-creates the cross-deliver bug)"*,
and it specifies the best-effort drop as a *"loud error-level LOGGED NO-OP,
surfaced via the ``DeliveryLedger``"*. The retry actuator calls the adapter
DIRECTLY, so on this path there is no ledger and nothing surfaces the no-op.
The contract was written; this one caller was never held to it.

THE RULE THIS PINS. An untargeted send is honest only on a channel that
addresses a single terminal implicitly (cli). On a channel that carries a
per-message target, "no target" means "no addressee", and the truthful action is
to send nothing and say so — never to guess a recipient, and never to report a
delivery that did not happen.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from stackowl.pipeline.retry_actuator import RetryActuator
from stackowl.pipeline.retry_attempt import RetryAttempt
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import ResponseChunk


def _row(**overrides: object) -> RetryAttempt:
    defaults = dict(
        id="retry-1", trace_id="trace-orig", session_key="sess-1",
        goal="find out why the job failed", banned_capabilities=[],
        attempt_count=0, status="pending", next_retry_at="", last_error=None,
        channel="telegram", channel_chat_id="", channel_message_id=None,
        created_at="", updated_at="",
    )
    defaults.update(overrides)
    return RetryAttempt(**defaults)  # type: ignore[arg-type]


def _answering_backend(text: str = "here is the answer") -> MagicMock:
    state = PipelineState(
        trace_id="trace-new", session_key="sess-1", input_text="g",
        channel="telegram", owl_name="secretary", pipeline_step="",
        responses=(
            ResponseChunk(
                content=text, is_final=True, chunk_index=0, trace_id="trace-new",
                owl_name="secretary", is_floor=False,
            ),
        ),
    )
    backend = MagicMock()
    backend.run = AsyncMock(return_value=state)
    return backend


def _adapter(*, implicitly_addressable: bool) -> MagicMock:
    """A double that models the ONE attribute the branch reads.

    Deliberately not a bare ``MagicMock``: every attribute of one is a truthy
    Mock, so a bare double answers "yes, I address a recipient implicitly" to a
    question it was never taught — which is how a test double stops resembling
    the thing it stands for.
    """
    adapter = MagicMock()
    adapter.implicitly_addressable = implicitly_addressable
    adapter.send_text = AsyncMock(return_value=None)
    adapter.edit_message = AsyncMock()
    return adapter


def _actuator(adapter: MagicMock, backend: MagicMock) -> RetryActuator:
    registry = MagicMock()
    registry.get = MagicMock(return_value=adapter)
    return RetryActuator(backend=backend, channel_registry=registry)


@pytest.mark.asyncio
async def test_an_unaddressed_retry_on_a_rich_channel_sends_to_nobody() -> None:
    """The 3-messages-to-the-wrong-chat half. No addressee ⇒ no send at all."""
    adapter = _adapter(implicitly_addressable=False)
    outcome = await _actuator(adapter, _answering_backend()).attempt_retry(
        _row(channel_chat_id="")
    )

    adapter.send_text.assert_not_awaited()
    adapter.edit_message.assert_not_awaited()
    assert outcome.delivered is False, (
        "the actuator must not report a delivery it did not make"
    )


@pytest.mark.asyncio
async def test_an_unaddressed_retry_is_not_reported_as_delivered() -> None:
    """The 60-discarded half: `delivered` is the field that used to not exist."""
    adapter = _adapter(implicitly_addressable=False)
    outcome = await _actuator(adapter, _answering_backend()).attempt_retry(_row())

    assert outcome.delivered is False


@pytest.mark.asyncio
async def test_a_single_terminal_channel_still_delivers_without_a_chat_id() -> None:
    """cli addresses its one terminal implicitly — this path must NOT regress."""
    adapter = _adapter(implicitly_addressable=True)
    outcome = await _actuator(adapter, _answering_backend()).attempt_retry(
        _row(channel="cli", channel_chat_id="")
    )

    adapter.send_text.assert_awaited_once()
    assert outcome.delivered is True


@pytest.mark.asyncio
async def test_an_addressed_retry_still_reaches_its_own_chat() -> None:
    """The targeted branch is untouched: the answer goes to the task's addressee."""
    adapter = _adapter(implicitly_addressable=False)
    outcome = await _actuator(adapter, _answering_backend()).attempt_retry(
        _row(channel_chat_id="72055773")
    )

    adapter.send_text.assert_awaited_once()
    assert adapter.send_text.await_args.kwargs["chat_id"] == 72055773
    assert outcome.delivered is True


def test_every_channel_carrying_a_per_message_target_declares_it() -> None:
    """The contract lives on the ADAPTERS, not in a list the actuator keeps.

    A name list inside the caller is the "two copies of one rule" shape and would
    drift the moment a channel is added. Each adapter answers for itself, and the
    base class default (True) is the single-terminal case.
    """
    from stackowl.channels.base import ChannelAdapter
    from stackowl.channels.cli_adapter import CLIAdapter
    from stackowl.channels.discord.adapter import DiscordChannelAdapter
    from stackowl.channels.slack.adapter import SlackChannelAdapter
    from stackowl.channels.telegram.adapter import TelegramChannelAdapter
    from stackowl.channels.whatsapp.adapter import WhatsAppChannelAdapter

    assert ChannelAdapter.implicitly_addressable is True
    assert CLIAdapter.implicitly_addressable is True
    for rich in (TelegramChannelAdapter, SlackChannelAdapter, DiscordChannelAdapter, WhatsAppChannelAdapter):
        assert rich.implicitly_addressable is False, (
            f"{rich.__name__} takes a per-message target, so an untargeted send "
            f"is a guess at a recipient, not a delivery"
        )
