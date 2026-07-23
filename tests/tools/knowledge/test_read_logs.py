"""Tests for ReadLogsTool — PATHFINDER-2026-07-22 Proposal 5.

CLAUDE.md documented read_logs with example queries for a long time before
any implementation existed (confirmed via a zero-hit grep before this tool
was built). These tests drive the REAL tool against a hand-built JSONL file
matching infra/observability.py's JsonlFormatter shape exactly.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from stackowl.tools.knowledge.read_logs import ReadLogsTool


def _line(
    *, ts: datetime, level: str = "INFO", module: str = "stackowl.tool",
    msg: str = "did a thing", trace_id: str | None = "t-1",
    duration_ms: float | None = None, fields: dict[str, object] | None = None,
) -> str:
    return json.dumps({
        "ts": ts.isoformat(),
        "level": level,
        "module": module,
        "msg": msg,
        "trace_id": trace_id,
        "span_id": None,
        "parent_span_id": None,
        "session_id": "s-1",
        "duration_ms": duration_ms,
        "fields": fields or {},
    })


def _write_log(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _tool(monkeypatch: pytest.MonkeyPatch, log_path: Path) -> ReadLogsTool:
    tool = ReadLogsTool()
    monkeypatch.setattr(ReadLogsTool, "_log_path", staticmethod(lambda: log_path))
    return tool


@pytest.mark.asyncio
async def test_no_log_file_yet_is_a_success_not_an_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    tool = _tool(monkeypatch, tmp_path / "missing.jsonl")
    result = await tool.execute()
    assert result.success is True
    assert "no log file yet" in result.output


@pytest.mark.asyncio
async def test_filters_by_trace_id(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    now = datetime.now(UTC)
    log_path = tmp_path / "stackowl.jsonl"
    _write_log(log_path, [
        _line(ts=now, trace_id="trace-A", msg="from A"),
        _line(ts=now, trace_id="trace-B", msg="from B"),
    ])
    tool = _tool(monkeypatch, log_path)

    result = await tool.execute(trace_id="trace-A")

    assert result.success is True
    assert "from A" in result.output
    assert "from B" not in result.output


@pytest.mark.asyncio
async def test_filters_by_level(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    now = datetime.now(UTC)
    log_path = tmp_path / "stackowl.jsonl"
    _write_log(log_path, [
        _line(ts=now, level="ERROR", msg="boom"),
        _line(ts=now, level="DEBUG", msg="quiet"),
    ])
    tool = _tool(monkeypatch, log_path)

    result = await tool.execute(level="error")  # lowercase input must still match

    assert "boom" in result.output
    assert "quiet" not in result.output


@pytest.mark.asyncio
async def test_filters_by_tool_field(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    now = datetime.now(UTC)
    log_path = tmp_path / "stackowl.jsonl"
    _write_log(log_path, [
        _line(ts=now, msg="shell ran", fields={"tool": "shell"}),
        _line(ts=now, msg="read_file ran", fields={"tool": "read_file"}),
    ])
    tool = _tool(monkeypatch, log_path)

    result = await tool.execute(tool="shell")

    assert "shell ran" in result.output
    assert "read_file ran" not in result.output


@pytest.mark.asyncio
async def test_since_minutes_excludes_old_lines(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    now = datetime.now(UTC)
    old = now - timedelta(hours=3)
    log_path = tmp_path / "stackowl.jsonl"
    _write_log(log_path, [
        _line(ts=old, msg="ancient"),
        _line(ts=now, msg="recent"),
    ])
    tool = _tool(monkeypatch, log_path)

    result = await tool.execute(since_minutes=60)

    assert "recent" in result.output
    assert "ancient" not in result.output


@pytest.mark.asyncio
async def test_limit_keeps_most_recent_matches(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    now = datetime.now(UTC)
    log_path = tmp_path / "stackowl.jsonl"
    lines = [_line(ts=now, msg=f"line-{i}") for i in range(5)]
    _write_log(log_path, lines)
    tool = _tool(monkeypatch, log_path)

    result = await tool.execute(limit=2)

    # deque(maxlen=2) keeps the LAST 2 lines read (file order == chronological).
    assert "line-3" in result.output
    assert "line-4" in result.output
    assert "line-0" not in result.output


@pytest.mark.asyncio
async def test_no_matches_returns_success_with_clear_message(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    now = datetime.now(UTC)
    log_path = tmp_path / "stackowl.jsonl"
    _write_log(log_path, [_line(ts=now, trace_id="trace-A")])
    tool = _tool(monkeypatch, log_path)

    result = await tool.execute(trace_id="trace-does-not-exist")

    assert result.success is True
    assert "no matching log lines" in result.output


@pytest.mark.asyncio
async def test_malformed_lines_are_skipped_not_fatal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    now = datetime.now(UTC)
    log_path = tmp_path / "stackowl.jsonl"
    _write_log(log_path, [
        "not json at all {{{",
        _line(ts=now, msg="the real one"),
    ])
    tool = _tool(monkeypatch, log_path)

    result = await tool.execute()

    assert result.success is True
    assert "the real one" in result.output


@pytest.mark.asyncio
async def test_duration_and_fields_render_when_present(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    now = datetime.now(UTC)
    log_path = tmp_path / "stackowl.jsonl"
    _write_log(log_path, [
        _line(ts=now, msg="slow call", duration_ms=1234.5, fields={"tool": "web_fetch"}),
    ])
    tool = _tool(monkeypatch, log_path)

    result = await tool.execute()

    assert "1234.5" in result.output
    assert "web_fetch" in result.output


def test_manifest_is_read_severity_and_knowledge_group() -> None:
    tool = ReadLogsTool()
    manifest = tool.manifest
    assert manifest.action_severity == "read"
    assert manifest.toolset_group == "knowledge"


def test_registered_in_the_builtin_registry() -> None:
    from stackowl.tools.registry import ToolRegistry

    registry = ToolRegistry.with_defaults()
    assert registry.get("read_logs") is not None
