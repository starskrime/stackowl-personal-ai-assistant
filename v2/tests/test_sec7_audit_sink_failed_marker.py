"""SEC-7 / F137 — a FAILED audit write is itself durably recorded.

When ``SecurityError``'s registered audit callback raises (e.g. the tamper-evident
audit_log INSERT fails), the security violation must NOT be downgraded to a single
ERROR log line. A durable ``audit_sink_failed`` marker is appended to a SEPARATE
sink under ``~/.stackowl`` so the operator can reconstruct that an audited security
event was lost.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from stackowl.exceptions import SecurityError
from stackowl.paths import StackowlHome


@pytest.fixture()
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path))
    return tmp_path


def test_failed_audit_write_appends_durable_marker(_home: Path) -> None:
    def _boom(event_type: str, ctx: dict[str, object]) -> None:
        raise RuntimeError("audit sink down")

    SecurityError.register_side_effects(audit_fn=_boom, notify_fn=None)
    try:
        SecurityError("traversal attempt", category="path_traversal", context={"path": "x"})
    finally:
        SecurityError.register_side_effects(audit_fn=None, notify_fn=None)

    marker = StackowlHome.audit_sink_failures_file()
    assert marker.exists()
    lines = [ln for ln in marker.read_text().splitlines() if ln.strip()]
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["marker"] == "audit_sink_failed"
    assert rec["category"] == "path_traversal"
    # The reason of the audit-sink failure is recorded (NOT a secret).
    assert "audit sink down" in rec["sink_error"]


def test_successful_audit_write_writes_no_marker(_home: Path) -> None:
    recorded: list[str] = []

    def _ok(event_type: str, ctx: dict[str, object]) -> None:
        recorded.append(event_type)

    SecurityError.register_side_effects(audit_fn=_ok, notify_fn=None)
    try:
        SecurityError("policy breach", category="policy_breach")
    finally:
        SecurityError.register_side_effects(audit_fn=None, notify_fn=None)

    assert recorded == ["security_violation"]
    marker = StackowlHome.audit_sink_failures_file()
    assert not marker.exists()  # no failure → no marker
