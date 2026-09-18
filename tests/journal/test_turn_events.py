"""``model.called``/``tool.called``/``delegation.hopped`` are registered
correctly (Story 2.7), and their three recording helpers are atomic with
their own transaction (AD-24), skip outside a real turn, and redact secrets
through a REAL emitter (NFR33) -- the same proof shape
``tests/journal/test_registry_and_leak_guard.py`` uses for Story 2.1's types.
"""

from __future__ import annotations

import json

import pytest

from stackowl.db.pool import DbPool
from stackowl.infra.trace import TraceContext
from stackowl.journal import AttentionClass, Outcome, RecordKind, classify
from stackowl.journal.enums import ToolCallErrorCode
from stackowl.journal.narrator import narrate_full
from stackowl.journal.registry import get_registry
from stackowl.journal.turn_events import (
    DelegationHoppedAttrs,
    ModelCalledAttrs,
    ToolCalledAttrs,
    record_delegation_hop,
    record_model_call,
    record_tool_call,
)

pytestmark = pytest.mark.asyncio

# Synthetic canary only -- never a real credential (mirrors
# test_registry_and_leak_guard.py's own BEARER_CANARY, 42 chars, comfortably
# under the 64-char attrs bound).
BEARER_CANARY = "Bearer sk-canary1234567890abcdefghijklmno"


class TestTheThreeTypesAreRegistered:
    def test_model_called_is_turn_ambient(self) -> None:
        spec = get_registry().get("model.called")
        assert spec.attrs_model is ModelCalledAttrs
        assert spec.record_kind is RecordKind.TURN
        assert spec.emitting_process == "providers.base"
        assert classify("model.called") == (AttentionClass.AMBIENT, None)

    def test_tool_called_is_turn_ambient(self) -> None:
        spec = get_registry().get("tool.called")
        assert spec.attrs_model is ToolCalledAttrs
        assert spec.record_kind is RecordKind.TURN
        assert spec.emitting_process == "pipeline.steps.execute"
        assert classify("tool.called") == (AttentionClass.AMBIENT, None)

    def test_delegation_hopped_is_turn_ambient(self) -> None:
        spec = get_registry().get("delegation.hopped")
        assert spec.attrs_model is DelegationHoppedAttrs
        assert spec.record_kind is RecordKind.TURN
        assert spec.emitting_process == "owls.a2a_delegation"
        assert classify("delegation.hopped") == (AttentionClass.AMBIENT, None)


class TestNarration:
    def test_model_called_names_the_provider(self) -> None:
        spec = get_registry().get("model.called")
        text = narrate_full(spec, ModelCalledAttrs(provider="anthropic", error_code=None), "x")
        assert "anthropic" in text

    def test_tool_called_names_the_tool(self) -> None:
        spec = get_registry().get("tool.called")
        text = narrate_full(
            spec,
            ToolCalledAttrs(
                tool_name="read_file", action_severity="read",
                effect_class=None, error_code=None,
            ),
            "x",
        )
        assert "read_file" in text

    def test_delegation_hopped_names_both_owls(self) -> None:
        spec = get_registry().get("delegation.hopped")
        text = narrate_full(
            spec, DelegationHoppedAttrs(from_owl="secretary", to_owl="scout", status="ok"), "x",
        )
        assert "secretary" in text and "scout" in text


class TestToolCalledAttrsNeverCarriesFreeText:
    def test_error_code_accepts_the_closed_enum_values(self) -> None:
        attrs = ToolCalledAttrs(
            tool_name="t", action_severity="read", effect_class=None,
            error_code=ToolCallErrorCode.TIMEOUT.value,
        )
        assert attrs.error_code == "timeout"

    def test_an_undeclared_field_is_refused(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ToolCalledAttrs(
                tool_name="t", action_severity="read", effect_class=None,
                error_code=None,
                raw_error="a raw exception string must never fit here",  # type: ignore[call-arg]
            )


class TestTheHelpersSkipOutsideARealTurn:
    """AD-24/Boundaries: recording fires ONLY when TraceContext.get()["trace_id"]
    is set -- a health-probe or other backgroundless call is skipped, never
    journaled with an empty trace."""

    async def test_record_model_call_with_no_trace_id_writes_no_row(
        self, tmp_db: DbPool
    ) -> None:
        await record_model_call(
            tmp_db, provider="no-trace-provider", duration_ms=1.0, ok=True, error_code=None,
        )
        rows = await tmp_db.fetch_all(
            "SELECT * FROM journal_events WHERE target_id = ?", ("no-trace-provider",)
        )
        assert rows == []

    async def test_record_tool_call_with_no_trace_id_writes_no_row(
        self, tmp_db: DbPool
    ) -> None:
        await record_tool_call(
            tmp_db, tool_name="no-trace-tool", action_severity="read", effect_class=None,
            duration_ms=1.0, outcome=Outcome.OK, error_code=None,
        )
        rows = await tmp_db.fetch_all(
            "SELECT * FROM journal_events WHERE target_id = ?", ("no-trace-tool",)
        )
        assert rows == []

    async def test_record_delegation_hop_with_no_trace_id_writes_no_row(
        self, tmp_db: DbPool
    ) -> None:
        await record_delegation_hop(
            tmp_db, from_owl="secretary", to_owl="no-trace-owl", status="ok", duration_ms=1.0,
        )
        rows = await tmp_db.fetch_all(
            "SELECT * FROM journal_events WHERE target_id = ?", ("no-trace-owl",)
        )
        assert rows == []

    async def test_a_none_db_pool_is_also_a_silent_no_op(self) -> None:
        """No DbPool wired at all (a standalone/test provider) must never raise."""
        token = TraceContext.start(trace_id="trace-no-pool")
        try:
            await record_model_call(
                None, provider="p", duration_ms=1.0, ok=True, error_code=None,
            )
        finally:
            TraceContext.reset(token)


class TestEachHelperCommitsExactlyOneRowInEachTable:
    async def test_record_model_call(self, tmp_db: DbPool) -> None:
        token = TraceContext.start(trace_id="trace-model-1", owl_name="scout")
        try:
            await record_model_call(
                tmp_db, provider="anthropic", duration_ms=123.4, ok=True, error_code=None,
            )
        finally:
            TraceContext.reset(token)

        turn_rows = await tmp_db.fetch_all(
            "SELECT * FROM turn_action_records WHERE trace_id = ?", ("trace-model-1",)
        )
        journal_rows = await tmp_db.fetch_all(
            "SELECT * FROM journal_events WHERE trace_id = ? AND type = 'model.called'",
            ("trace-model-1",),
        )
        assert len(turn_rows) == 1
        assert len(journal_rows) == 1
        row = journal_rows[0]
        assert row["actor_kind"] == "owl"
        assert row["actor_id"] == "scout"
        assert row["target_kind"] == "owner"
        assert row["target_id"] == "anthropic"
        assert row["outcome"] == "ok"
        assert row["attention"] == "ambient"
        ref = json.loads(row["record_ref"])
        assert ref["kind"] == "sqlite"
        assert ref["locator"]["table"] == "turn_action_records"
        assert ref["locator"]["id"] == turn_rows[0]["id"]
        assert turn_rows[0]["kind"] == "model.called"
        assert turn_rows[0]["identifier"] == "anthropic"

    async def test_record_tool_call_failed(self, tmp_db: DbPool) -> None:
        token = TraceContext.start(trace_id="trace-tool-1", owl_name="scout")
        try:
            await record_tool_call(
                tmp_db, tool_name="read_file", action_severity="read",
                effect_class=None, duration_ms=5.5, outcome=Outcome.FAILED,
                error_code=ToolCallErrorCode.TIMEOUT.value,
            )
        finally:
            TraceContext.reset(token)

        journal_rows = await tmp_db.fetch_all(
            "SELECT * FROM journal_events WHERE trace_id = ? AND type = 'tool.called'",
            ("trace-tool-1",),
        )
        assert len(journal_rows) == 1
        assert journal_rows[0]["target_kind"] == "owner"
        assert journal_rows[0]["target_id"] == "read_file"
        assert journal_rows[0]["outcome"] == "failed"
        attrs = json.loads(journal_rows[0]["attrs"])
        assert attrs["error_code"] == "timeout"

    async def test_record_delegation_hop_targets_the_owl(self, tmp_db: DbPool) -> None:
        token = TraceContext.start(trace_id="trace-deleg-1", owl_name="secretary")
        try:
            await record_delegation_hop(
                tmp_db, from_owl="secretary", to_owl="scout", status="ok", duration_ms=99.0,
            )
        finally:
            TraceContext.reset(token)

        journal_rows = await tmp_db.fetch_all(
            "SELECT * FROM journal_events WHERE trace_id = ? AND type = 'delegation.hopped'",
            ("trace-deleg-1",),
        )
        assert len(journal_rows) == 1
        row = journal_rows[0]
        # AD-3's one closed list: a delegation hop's target genuinely IS
        # another owl (Design Notes) -- the one place this story targets OWL.
        assert row["target_kind"] == "owl"
        assert row["target_id"] == "scout"
        assert row["outcome"] == "ok"
        attrs = json.loads(row["attrs"])
        assert attrs["status"] == "ok"

    @pytest.mark.parametrize("status", ["timeout", "child_error", "cycle", "off_topic"])
    async def test_delegation_hop_non_ok_statuses_collapse_to_failed(
        self, tmp_db: DbPool, status: str,
    ) -> None:
        token = TraceContext.start(trace_id=f"trace-deleg-{status}", owl_name="secretary")
        try:
            await record_delegation_hop(
                tmp_db, from_owl="secretary", to_owl="scout", status=status, duration_ms=1.0,
            )
        finally:
            TraceContext.reset(token)

        rows = await tmp_db.fetch_all(
            "SELECT outcome, attrs FROM journal_events WHERE trace_id = ? "
            "AND type = 'delegation.hopped'", (f"trace-deleg-{status}",),
        )
        assert rows[0]["outcome"] == "failed"
        assert json.loads(rows[0]["attrs"])["status"] == status

    async def test_delegation_hop_empty_status_is_ok(self, tmp_db: DbPool) -> None:
        token = TraceContext.start(trace_id="trace-deleg-empty", owl_name="secretary")
        try:
            await record_delegation_hop(
                tmp_db, from_owl="secretary", to_owl="scout", status="empty", duration_ms=1.0,
            )
        finally:
            TraceContext.reset(token)

        rows = await tmp_db.fetch_all(
            "SELECT outcome FROM journal_events WHERE trace_id = ? "
            "AND type = 'delegation.hopped'", ("trace-deleg-empty",),
        )
        assert rows[0]["outcome"] == "ok"


class TestTheHelperIsAtomicWithItsOwnTransaction:
    """AD-24, applied to these three sites specifically: the turn_action_records
    row and its journal.record() call share ONE commit."""

    async def test_a_journal_record_failure_leaves_neither_row(
        self, tmp_db: DbPool, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from stackowl.journal import turn_events as turn_events_module

        async def _boom(*_args: object, **_kwargs: object) -> str:
            raise RuntimeError("simulated journal.record failure")

        monkeypatch.setattr(turn_events_module, "journal_record", _boom)

        token = TraceContext.start(trace_id="trace-rollback-1", owl_name="scout")
        try:
            # Never raises out (B5) -- the caller (a real provider/tool/
            # delegation call site) must never see this exception.
            await record_tool_call(
                tmp_db, tool_name="t-rollback", action_severity="read",
                effect_class=None, duration_ms=1.0, outcome=Outcome.OK, error_code=None,
            )
        finally:
            TraceContext.reset(token)

        turn_rows = await tmp_db.fetch_all(
            "SELECT * FROM turn_action_records WHERE trace_id = ?", ("trace-rollback-1",)
        )
        journal_rows = await tmp_db.fetch_all(
            "SELECT * FROM journal_events WHERE trace_id = ?", ("trace-rollback-1",)
        )
        assert turn_rows == []
        assert journal_rows == []


class TestCanarySecretsAreRedactedThroughRealEmitters:
    """NFR33: driven through each of the three REAL recording helpers -- no
    canary ever appears unredacted in ``journal_events.attrs``."""

    async def test_model_called(self, tmp_db: DbPool) -> None:
        token = TraceContext.start(trace_id="trace-canary-model")
        try:
            await record_model_call(
                tmp_db, provider=BEARER_CANARY, duration_ms=1.0, ok=True, error_code=None,
            )
        finally:
            TraceContext.reset(token)

        rows = await tmp_db.fetch_all(
            "SELECT attrs FROM journal_events WHERE trace_id = ? AND type = 'model.called'",
            ("trace-canary-model",),
        )
        assert len(rows) == 1
        stored = rows[0]["attrs"]
        assert BEARER_CANARY not in stored
        assert json.loads(stored)["_redacted"] is True

    async def test_tool_called(self, tmp_db: DbPool) -> None:
        token = TraceContext.start(trace_id="trace-canary-tool")
        try:
            await record_tool_call(
                tmp_db, tool_name=BEARER_CANARY, action_severity="read",
                effect_class=None, duration_ms=1.0, outcome=Outcome.OK, error_code=None,
            )
        finally:
            TraceContext.reset(token)

        rows = await tmp_db.fetch_all(
            "SELECT attrs FROM journal_events WHERE trace_id = ? AND type = 'tool.called'",
            ("trace-canary-tool",),
        )
        assert len(rows) == 1
        stored = rows[0]["attrs"]
        assert BEARER_CANARY not in stored
        assert json.loads(stored)["_redacted"] is True

    async def test_delegation_hopped(self, tmp_db: DbPool) -> None:
        token = TraceContext.start(trace_id="trace-canary-deleg")
        try:
            await record_delegation_hop(
                tmp_db, from_owl="secretary", to_owl=BEARER_CANARY,
                status="ok", duration_ms=1.0,
            )
        finally:
            TraceContext.reset(token)

        rows = await tmp_db.fetch_all(
            "SELECT attrs FROM journal_events WHERE trace_id = ? "
            "AND type = 'delegation.hopped'", ("trace-canary-deleg",),
        )
        assert len(rows) == 1
        stored = rows[0]["attrs"]
        assert BEARER_CANARY not in stored
        assert json.loads(stored)["_redacted"] is True
