"""Story 2.9 -- gateway-role channel ingress records ``channel.message_received``
through the GATEWAY's own ``db_pool`` (AD-9).

``startup/orchestrator.py``'s ``_message_loop``/``_telegram_loop``/
``_slack_loop``/``_discord_loop``/``_whatsapp_loop`` are closures nested
inside ``_phase_gateway`` and unreachable from outside it -- the same shape
``tests/startup/test_deliver_command_stub.py`` documents for
``_deliver_command_stub`` ("this closure is unreachable from outside
_phase_gateway, so the test hand-copies it"). This test hand-copies the ONE
line every one of the 5 loops gained, byte-for-byte identical across all 5
(orchestrator.py's own Code Map: "each gets the identical one-line helper
call inserted right after ``msg = await adapter.receive()`` succeeds"), so
proving it once here proves the shape shared by all 5.
"""

from __future__ import annotations

import json

import pytest

from stackowl.db.pool import DbPool
from stackowl.gateway.scanner import IngressMessage
from stackowl.journal.channel_events import record_channel_message_received

pytestmark = pytest.mark.asyncio


class _FakeAdapter:
    """A minimal channel adapter: ``receive()`` returns queued messages, or
    raises when the queue is exhausted (mirrors a real adapter's own
    "nothing to receive yet" branch, which the orchestrator's own
    ``except Exception`` retry branch already handles without ever calling
    the helper -- the "no row on a raised receive()" edge case)."""

    def __init__(self, messages: list[IngressMessage]) -> None:
        self._messages = list(messages)

    async def receive(self) -> IngressMessage:
        if not self._messages:
            raise RuntimeError("no more messages queued")
        return self._messages.pop(0)


async def _receive_loop_slice(adapter: _FakeAdapter, db_pool: DbPool) -> IngressMessage | None:
    """Hand-copy of the shared slice every one of the 5 gateway receive loops
    gained: ``msg = await adapter.receive()`` succeeds, then, BEFORE the turn
    is submitted, ``record_channel_message_received`` is called with the
    message's own ``channel``/``session_key``/``trace_id`` — mirrors
    ``orchestrator.py::_message_loop`` (and byte-for-byte, the other 4)."""
    try:
        msg = await adapter.receive()
    except Exception:
        return None
    await record_channel_message_received(
        db_pool, channel=msg.channel, session_key=msg.session_key, trace_id=msg.trace_id,
    )
    return msg


class TestARealInboundMessageIsRecorded:
    async def test_channel_message_received_lands_with_channel_and_session_key(
        self, tmp_db: DbPool,
    ) -> None:
        msg = IngressMessage(
            text="hello", session_key="telegram:12345", channel="telegram",
            trace_id="trace-ingress-1",
        )
        adapter = _FakeAdapter([msg])

        received = await _receive_loop_slice(adapter, tmp_db)

        assert received is msg
        journal_rows = await tmp_db.fetch_all(
            "SELECT * FROM journal_events WHERE type = 'channel.message_received'",
        )
        assert len(journal_rows) == 1
        row = journal_rows[0]
        assert row["outcome"] == "ok"
        assert row["trace_id"] == "trace-ingress-1"
        attrs = json.loads(row["attrs"])
        assert attrs["channel"] == "telegram"
        assert attrs["session_key"] == "telegram:12345"

        ingress_rows = await tmp_db.fetch_all("SELECT * FROM channel_ingress_records")
        assert len(ingress_rows) == 1
        assert ingress_rows[0]["channel"] == "telegram"
        assert ingress_rows[0]["session_key"] == "telegram:12345"


class TestAReceiveThatRaisesRecordsNothing:
    async def test_no_row_when_receive_raises(self, tmp_db: DbPool) -> None:
        adapter = _FakeAdapter([])  # empty queue -> receive() raises

        received = await _receive_loop_slice(adapter, tmp_db)

        assert received is None
        journal_rows = await tmp_db.fetch_all(
            "SELECT * FROM journal_events WHERE type = 'channel.message_received'",
        )
        assert journal_rows == []


class TestEachOfTheFiveLoopsCallsTheHelperOnce:
    """Source-level tripwire: every one of the 5 gateway receive loops --
    ``_message_loop``/``_telegram_loop``/``_slack_loop``/``_discord_loop``/
    ``_whatsapp_loop``, each nested inside ``_phase_gateway`` -- individually
    calls ``record_channel_message_received`` with the documented
    ``channel=msg.channel, session_key=msg.session_key, trace_id=msg.trace_id``
    keywords.

    AST-scoped to ``_phase_gateway``'s own body, mirroring the pattern
    ``tests/startup/test_journal_name_resolver_wiring.py`` and
    ``tests/startup/test_core_link_secret_wiring.py`` already use for this
    exact "boot is impractical, so parse the AST instead" situation: a
    whole-module substring count (the prior version of this test) would miss
    a wrong keyword argument or a call misplaced into the wrong loop, since
    it can't tell which loop a given call site belongs to.
    """

    _LOOP_NAMES = (
        "_message_loop", "_telegram_loop", "_slack_loop",
        "_discord_loop", "_whatsapp_loop",
    )
    _EXPECTED_KEYWORDS = {"channel": "channel", "session_key": "session_key", "trace_id": "trace_id"}

    def test_five_call_sites_exist(self) -> None:
        import ast
        import inspect
        import textwrap

        from stackowl.startup import orchestrator as orch_mod

        src = textwrap.dedent(inspect.getsource(orch_mod.StartupOrchestrator._phase_gateway))
        mod = ast.parse(src)
        phase_gateway = mod.body[0]
        assert isinstance(phase_gateway, ast.AsyncFunctionDef), (
            "expected _phase_gateway to be an async def"
        )

        for loop_name in self._LOOP_NAMES:
            loop_defs = [
                n
                for n in ast.walk(phase_gateway)
                if isinstance(n, ast.AsyncFunctionDef) and n.name == loop_name
            ]
            assert len(loop_defs) == 1, (
                f"expected exactly one nested `{loop_name}` def in _phase_gateway, "
                f"found {len(loop_defs)}"
            )
            loop_fn = loop_defs[0]

            calls = [
                n
                for n in ast.walk(loop_fn)
                if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Name)
                and n.func.id == "record_channel_message_received"
            ]
            assert len(calls) == 1, (
                f"expected exactly one call to record_channel_message_received in "
                f"`{loop_name}`, found {len(calls)}"
            )
            call = calls[0]

            actual_keywords = {
                kw.arg: ast.unparse(kw.value) for kw in call.keywords if kw.arg is not None
            }
            for expected_arg, expected_attr in self._EXPECTED_KEYWORDS.items():
                assert expected_arg in actual_keywords, (
                    f"`{loop_name}`'s record_channel_message_received call is missing "
                    f"keyword `{expected_arg}`"
                )
                assert actual_keywords[expected_arg] == f"msg.{expected_attr}", (
                    f"`{loop_name}`'s record_channel_message_received call passes "
                    f"{expected_arg}={actual_keywords[expected_arg]!r}, expected "
                    f"{expected_arg}=msg.{expected_attr}"
                )
