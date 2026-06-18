"""Tests for BatchAuditLogger — single chained row, no audit_logger handling."""

from __future__ import annotations

from typing import Any

from stackowl.audit.batch_logger import BatchAuditLogger


class _RecordingAuditLogger:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def append(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


class TestBatchAuditLogger:
    def test_no_audit_logger_is_noop(self) -> None:
        with BatchAuditLogger(None, event_type="browser_browse", actor="scout", target="x") as b:
            b.add_step({"action": "navigate"})
        # No-op: no exception.

    def test_commits_one_row_with_steps(self) -> None:
        rec = _RecordingAuditLogger()
        with BatchAuditLogger(rec, event_type="browser_browse", actor="scout", target="https://x") as b:
            b.add_step({"action": "navigate"})
            b.add_step({"action": "click_index", "index": 4})
            b.add_step({"action": "done"})
        assert len(rec.calls) == 1
        call = rec.calls[0]
        assert call["event_type"] == "browser_browse"
        assert call["actor"] == "scout"
        assert call["target"] == "https://x"
        assert call["details"]["step_count"] == 3
        assert call["details"]["steps"][0]["action"] == "navigate"
        assert call["details"]["exception"] is None

    def test_records_exception_in_details(self) -> None:
        rec = _RecordingAuditLogger()
        try:
            with BatchAuditLogger(rec, event_type="browser_browse", actor="scout", target=None) as b:
                b.add_step({"action": "navigate"})
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        assert rec.calls[0]["details"]["exception"] == "RuntimeError: boom"

    def test_add_step_after_exit_is_noop(self) -> None:
        rec = _RecordingAuditLogger()
        b = BatchAuditLogger(rec, event_type="browser_browse", actor="scout", target=None)
        with b:
            b.add_step({"action": "a"})
        b.add_step({"action": "after_exit"})
        # Only the in-context step is persisted.
        assert rec.calls[0]["details"]["step_count"] == 1

    def test_extra_details_merged(self) -> None:
        rec = _RecordingAuditLogger()
        with BatchAuditLogger(
            rec, event_type="browser_browse", actor="scout", target=None,
            extra_details={"task_len": 42, "allowed_domains": ["x.com"]},
        ) as b:
            b.add_step({"action": "done"})
        details = rec.calls[0]["details"]
        assert details["task_len"] == 42
        assert details["allowed_domains"] == ["x.com"]

    def test_append_failure_is_swallowed(self) -> None:
        class _Broken:
            def append(self, **kwargs: Any) -> None:
                raise RuntimeError("db full")
        # Should NOT propagate.
        with BatchAuditLogger(_Broken(), event_type="x", actor="y", target=None) as b:
            b.add_step({"action": "a"})
