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

import re
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


def test_the_core_side_PROXY_answers_for_the_channel_it_proxies_not_for_itself() -> None:
    """THE FIX ABOVE DID NOT WORK IN PRODUCTION, and this is why.

    MEASURED LIVE 2026-09-08, by enqueuing a bounded task with destination
    "telegram" (a bare channel name) on the running platform and watching it::

        04:50:46.512  retry_actuator.attempt_retry: exit   {"delivered": true}
        04:50:46.514  [telegram] adapter.send_text: no active chat
                      (best-effort) — message dropped

    `delivered: true`, and the answer dropped two milliseconds later. In the
    SPLIT-PROCESS deployment the core does not hold the telegram adapter — the
    gateway does — so `channel_registry.get("telegram")` returns a
    `SocketChannelAdapter`, a proxy that forwards a frame over the socket. It
    subclasses `ChannelAdapter`, so it inherited the base default and answered
    "yes, I address a recipient implicitly" on behalf of a channel that cannot.

    The proxy loses the identity of the thing it stands for — and the property
    directly ABOVE this one in `socket_adapter.py` carries a docstring warning
    about that exact mistake for `surface`. A new class attribute walked into it
    the very next property down.

    The unit tests above could not catch this: they hand the actuator a double
    that models the attribute, which is the real adapter's shape, not the
    proxy's. Only the live run distinguished them.
    """
    from stackowl.channels.socket_adapter import (
        _GATEWAY_HELD_CHANNELS,
        SocketChannelAdapter,
    )

    telegram_proxy = SocketChannelAdapter(object(), channel_name="telegram")  # type: ignore[arg-type]
    assert telegram_proxy.implicitly_addressable is False, (
        "the core's proxy for telegram must not promise a delivery the gateway's "
        "adapter cannot make without a chat id"
    )

    cli_proxy = SocketChannelAdapter(object(), channel_name="cli")  # type: ignore[arg-type]
    assert cli_proxy.implicitly_addressable is True, (
        "the cli proxy stands for a single-terminal adapter and must NOT regress"
    )
    assert "cli" not in _GATEWAY_HELD_CHANNELS


def test_the_proxy_list_cannot_drift_from_the_channels_the_gateway_actually_runs() -> None:
    """One source, and the other asks it.

    `configured_gateway_channels` decides which channels get a socket proxy;
    `_GATEWAY_HELD_CHANNELS` decides what those proxies claim about addressing.
    Two lists of the same four names is the "two copies of one rule" shape, so
    this pins them together: every channel the first can return must appear in
    the second. Its own docstring already carries a `ponytail:` conceding the
    duplication of the orchestrator's gates — this stops a THIRD copy drifting.
    """
    import inspect

    from stackowl.channels import socket_adapter

    src = inspect.getsource(socket_adapter.configured_gateway_channels)
    appended = set(re.findall(r'channels\.append\("([a-z]+)"\)', src))
    assert appended, "could not read the channel names out of the function"
    missing = appended - set(socket_adapter._GATEWAY_HELD_CHANNELS)
    assert not missing, (
        f"{sorted(missing)} get a socket proxy but are absent from "
        f"_GATEWAY_HELD_CHANNELS, so their proxies would claim to address a "
        f"recipient implicitly"
    )


@pytest.mark.asyncio
async def test_the_loop_does_not_stamp_a_delivery_for_an_answer_nobody_received() -> None:
    """THE SECOND THING THE LIVE RUN CAUGHT, one level above the adapter.

    With `_deliver_success` fixed, the 05:03 run sent nothing — correctly — and
    the loop STILL wrote::

        [loop] task COMPLETE — its outcome reached its destination
        {"delivered_at": "2026-09-08T05:03:29.162176+00:00"}

    because `_dispatch` marks delivery on any non-empty result string, and the
    runner returned one. Making that string truthful was not enough: the loop had
    only two terminals, delivered or failed, so an answer with no addressee had to
    be filed as one of them. `delivered_at` is a PROOF column; it must stay NULL.
    """
    from stackowl.pipeline.durable.addressing import NoAddresseeCompletion
    from stackowl.pipeline.durable.loop import TaskLoop

    store = MagicMock()
    store.mark_delivered = AsyncMock()
    store.mark_completed_unaddressed = AsyncMock()
    store.reclaim_expired = AsyncMock()
    store.count_prior_reshaping_failures = AsyncMock(return_value=0)

    async def _runner(_task: object) -> str:
        raise NoAddresseeCompletion("re-driven; nobody to deliver to")

    loop = TaskLoop.__new__(TaskLoop)
    loop._store = store            # noqa: SLF001
    loop._runner = _runner         # noqa: SLF001
    task = MagicMock()
    task.task_id = "t-unaddressed"
    task.last_failure_class = ""

    await loop._dispatch(task)     # noqa: SLF001

    store.mark_delivered.assert_not_awaited()
    store.mark_completed_unaddressed.assert_awaited_once()
    assert store.mark_completed_unaddressed.await_args.kwargs["result"] == (
        "re-driven; nobody to deliver to"
    )


def test_the_unaddressed_terminal_leaves_the_delivery_proof_alone() -> None:
    """`delivered_at` is proof the answer landed. It must not appear in the SQL."""
    import ast
    import inspect
    import textwrap

    from stackowl.pipeline.durable.store import DurableTaskStore

    # READ THE SQL, NOT THE SOURCE TEXT. The first version of this asserted over
    # `inspect.getsource(...)` and failed on the method's own DOCSTRING, which
    # explains why `delivered_at` must stay NULL. A guard satisfied — or broken —
    # by prose is not a guard on behaviour; ask the AST for the string literals.
    tree = ast.parse(textwrap.dedent(
        inspect.getsource(DurableTaskStore.mark_completed_unaddressed)
    ))
    fn = tree.body[0]
    assert isinstance(fn, ast.AsyncFunctionDef)
    body = fn.body[1:] if ast.get_docstring(fn) else fn.body
    literals = [
        n.value for n in ast.walk(ast.Module(body=body, type_ignores=[]))
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    ]
    sql = " ".join(literals)
    assert "delivered_at" not in sql, (
        "stamping delivered_at here would re-create the exact overclaim this "
        "terminal exists to prevent"
    )
    assert "acknowledged_at" in sql
    assert "status='completed'" in sql
