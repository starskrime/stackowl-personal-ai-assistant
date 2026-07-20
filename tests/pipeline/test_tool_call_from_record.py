"""_tool_call_from_record — every ToolCall construction site in execute.py must
populate ``error`` from the provider's own computed ``"failed"`` flag, not
hardcode None. Regression for the bug where consolidate.py's F095 merge filter
(``tc.error is None``) was a silent no-op because every construction site set
error=None unconditionally, letting a failed tool's raw error text ship to the
user as if it were the answer.
"""

from __future__ import annotations

from stackowl.pipeline.steps.execute import _tool_call_from_record


def test_failed_record_populates_error() -> None:
    rc = {"name": "shell", "args": {"cmd": "rm -rf x"}, "result": "rm: permission denied", "failed": True}
    tc = _tool_call_from_record(rc)
    assert tc.error == "rm: permission denied"
    assert tc.result == "rm: permission denied"


def test_successful_record_leaves_error_none() -> None:
    rc = {"name": "read_file", "args": {}, "result": "file contents", "failed": False}
    tc = _tool_call_from_record(rc)
    assert tc.error is None
    assert tc.result == "file contents"


def test_missing_failed_key_defaults_to_success() -> None:
    """A record with no 'failed' key at all (defensive: shouldn't happen given
    both providers always set it) must not be treated as a failure."""
    rc = {"name": "read_file", "args": {}, "result": "ok"}
    tc = _tool_call_from_record(rc)
    assert tc.error is None


def test_name_and_args_are_preserved() -> None:
    rc = {"name": "web_fetch", "args": {"url": "https://x.test"}, "result": "ok", "failed": False}
    tc = _tool_call_from_record(rc)
    assert tc.tool_name == "web_fetch"
    assert tc.args == {"url": "https://x.test"}


def test_missing_args_defaults_to_empty_dict() -> None:
    rc = {"name": "shell", "result": "ok", "failed": False}
    tc = _tool_call_from_record(rc)
    assert tc.args == {}


def test_duration_ms_override() -> None:
    rc = {"name": "shell", "result": "ok", "failed": False}
    tc = _tool_call_from_record(rc, duration_ms=42.0)
    assert tc.duration_ms == 42.0
