"""/audit sub-command discoverability — the WS-A proof slice.

Closes the documented gap: previously `/audit foo` dumped the chain with no
help, and `export` was undiscoverable at runtime.
"""

from __future__ import annotations

import pytest

from stackowl.commands.audit import AuditCommand


def _state():  # type: ignore[no-untyped-def]
    from stackowl.pipeline.state import PipelineState

    return PipelineState(
        trace_id="trace-1",
        session_id="test-session",
        input_text="",
        channel="cli",
        owl_name="Daria",
        pipeline_step="receive",
    )


def test_audit_declares_export_subcommand() -> None:
    cmd = AuditCommand()
    names = {s.name for s in cmd.meta.subcommands}
    assert names == {"export"}
    export = cmd.meta.subcommands[0]
    assert export.summary  # non-empty one-liner
    assert any(a.name == "--output" for a in export.args)


@pytest.mark.asyncio
async def test_unknown_subcommand_returns_usage_not_chain_dump() -> None:
    """`/audit bogus` shows the auto-generated usage (the fixed gap)."""
    cmd = AuditCommand(audit_logger=_FakeLogger())
    out = await cmd.handle("bogus", _state())
    assert "Usage: /audit" in out
    assert "export" in out
    # It must NOT be the chain/table dump.
    assert "Chain intact" not in out


@pytest.mark.asyncio
async def test_bare_audit_still_shows_the_tail() -> None:
    """A bare `/audit` keeps the default tail view — not usage."""
    cmd = AuditCommand(audit_logger=_FakeLogger())
    out = await cmd.handle("", _state())
    assert "Chain intact" in out


class _FakeLogger:
    """Minimal stand-in for AuditLogger for the default tail view."""

    def tail(self, n: int):  # type: ignore[no-untyped-def]
        return []

    def verify_chain(self):  # type: ignore[no-untyped-def]
        return True, None
