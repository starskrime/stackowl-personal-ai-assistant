"""Story 6.7 (part A) — /memory remember/forget/export tests.

The /staged half went with the command in D08.1.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from stackowl.commands.memory_command import MemoryCommand
from stackowl.commands.registry import CommandRegistry
from stackowl.commands.response import Action, CommandResponse
from stackowl.config.test_mode import TestModeGuard, TestModeViolation
from tests._story_6_7_helpers import (  # noqa: F401 — fixture re-exports
    EventBus,
    FakeBridge,
    FakePromoter,
    db,
    make_record,
    make_settings,
    make_staged,
    make_state,
    no_test_mode_guard,
)


def _reset_registry() -> None:
    CommandRegistry.reset()


def _text(out: object) -> str:
    """Unwrap a CommandResponse to its text, or pass through a plain str."""
    return out.text if hasattr(out, "text") else out  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# StagedCommand — list / review / reject / promote
# ---------------------------------------------------------------------------

# The seven /staged tests that stood here went with the command (D08.1). It
# listed, reviewed, approved and rejected STAGED facts; extraction is retired,
# so the queue it reviewed is permanently empty.



async def test_memory_remember_stages_and_promotes() -> None:
    _reset_registry()
    bridge = FakeBridge()
    promoter = FakePromoter(success=True)
    cmd = MemoryCommand(
        bridge=bridge,
        settings=make_settings(),
        db=object(),  # type: ignore[arg-type]
        event_bus=EventBus(),
        promoter=promoter,  # type: ignore[arg-type]
    )
    out = await cmd.handle("remember the moon orbits earth", make_state())
    assert len(bridge.staged) == 1
    staged = bridge.staged[0]
    assert staged.content == "the moon orbits earth"
    assert staged.source_type == "manual"
    assert staged.confidence == 1.0
    assert staged.reinforcement_count == 3
    assert promoter.force_calls == [staged.fact_id]
    assert "Remembered" in out


async def test_memory_forget_calls_delete() -> None:
    _reset_registry()
    fid = "forget-id-bbbb-0000-0000-0000-000000000001"
    bridge = FakeBridge()
    bridge.seed("staged", make_staged(fact_id=fid))
    cmd = MemoryCommand(
        bridge=bridge,
        settings=make_settings(),
        db=object(),  # type: ignore[arg-type]
        event_bus=EventBus(),
        promoter=FakePromoter(),  # type: ignore[arg-type]
    )
    out = await cmd.handle(f"forget {fid} YES", make_state())
    assert bridge.delete_calls == [fid]
    assert "Forgotten" in out


async def test_memory_forget_unknown_prefix_returns_error() -> None:
    _reset_registry()
    bridge = FakeBridge()
    cmd = MemoryCommand(
        bridge=bridge,
        settings=make_settings(),
        db=object(),  # type: ignore[arg-type]
        event_bus=EventBus(),
        promoter=FakePromoter(),  # type: ignore[arg-type]
    )
    out = await cmd.handle("forget nonexistent-prefix YES", make_state())
    assert "no fact matches" in out.lower()
    assert bridge.delete_calls == []


async def test_memory_search_hits_return_actions() -> None:
    _reset_registry()
    bridge = FakeBridge()
    bridge.recall_results = [make_record(fact_id="rec-1", content="the sky is blue")]
    cmd = MemoryCommand(
        bridge=bridge,
        settings=make_settings(),
        db=object(),  # type: ignore[arg-type]
        event_bus=EventBus(),
        promoter=FakePromoter(),  # type: ignore[arg-type]
    )
    out = await cmd.handle("search sky", make_state())
    assert isinstance(out, CommandResponse)
    assert "the sky is blue" in out.text
    assert out.actions == (
        Action(label="the sky is blue", command="/memory menu rec-1", destructive=False),
    )


async def test_memory_menu_shows_fact_and_forget_action() -> None:
    _reset_registry()
    fid = "menu-id-cccc-0000-0000-0000-000000000001"
    bridge = FakeBridge()
    bridge.seed("staged", make_staged(fact_id=fid, content="menu target fact"))
    cmd = MemoryCommand(
        bridge=bridge,
        settings=make_settings(),
        db=object(),  # type: ignore[arg-type]
        event_bus=EventBus(),
        promoter=FakePromoter(),  # type: ignore[arg-type]
    )
    out = await cmd.handle(f"menu {fid}", make_state())
    assert isinstance(out, CommandResponse)
    assert "menu target fact" in out.text
    assert out.actions == (
        Action(
            label=f"Forget {fid[:8]}",
            command=f"/memory forget {fid} YES",
            destructive=True,
        ),
    )


async def test_memory_menu_unknown_id_returns_error() -> None:
    _reset_registry()
    bridge = FakeBridge()
    cmd = MemoryCommand(
        bridge=bridge,
        settings=make_settings(),
        db=object(),  # type: ignore[arg-type]
        event_bus=EventBus(),
        promoter=FakePromoter(),  # type: ignore[arg-type]
    )
    out = await cmd.handle("menu nonexistent", make_state())
    assert "no fact matches" in out.lower()  # type: ignore[union-attr]


async def test_memory_export_json_format() -> None:
    _reset_registry()
    bridge = FakeBridge()
    bridge.seed(
        "committed",
        make_staged(content="committed alpha", status="committed"),
    )
    bridge.seed(
        "committed",
        make_staged(
            fact_id="fff00000-0000-0000-0000-000000000002",
            content="committed beta",
            status="committed",
        ),
    )
    cmd = MemoryCommand(
        bridge=bridge,
        settings=make_settings(),
        db=object(),  # type: ignore[arg-type]
        event_bus=EventBus(),
    )
    out = await cmd.handle("export", make_state())
    parsed = json.loads(out)
    assert len(parsed) == 2
    contents = {r["content"] for r in parsed}
    assert {"committed alpha", "committed beta"} <= contents


async def test_memory_export_csv_format() -> None:
    _reset_registry()
    bridge = FakeBridge()
    bridge.seed(
        "committed",
        make_staged(content="csv content", status="committed"),
    )
    cmd = MemoryCommand(
        bridge=bridge,
        settings=make_settings(),
        db=object(),  # type: ignore[arg-type]
        event_bus=EventBus(),
    )
    out = await cmd.handle("export --format csv", make_state())
    lines = out.strip().splitlines()
    assert lines[0] == "fact_id,content,confidence,committed_at,source_type"
    assert any("csv content" in line for line in lines[1:])


async def test_memory_export_writes_output_file(tmp_path: Path) -> None:
    _reset_registry()
    bridge = FakeBridge()
    bridge.seed(
        "committed",
        make_staged(content="file payload", status="committed"),
    )
    cmd = MemoryCommand(
        bridge=bridge,
        settings=make_settings(),
        db=object(),  # type: ignore[arg-type]
        event_bus=EventBus(),
    )
    out_path = tmp_path / "export.json"
    out = await cmd.handle(
        f"export --format json --output {out_path}", make_state()
    )
    assert out_path.exists()
    parsed = json.loads(out_path.read_text(encoding="utf-8"))
    assert any(r["content"] == "file payload" for r in parsed)
    assert "Exported 1 facts" in out


async def test_memory_export_calls_test_mode_guard_for_file_io(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _reset_registry()

    # Re-install the real guard for this single test (overrides autouse fixture).
    def _real_guard(operation: str) -> None:
        if TestModeGuard._active:  # type: ignore[attr-defined]
            raise TestModeViolation(operation)

    monkeypatch.setattr(
        "stackowl.config.test_mode.TestModeGuard.assert_not_test_mode",
        classmethod(lambda cls, op: _real_guard(op)),
    )
    TestModeGuard.activate()
    try:
        bridge = FakeBridge()
        bridge.seed(
            "committed",
            make_staged(content="guarded", status="committed"),
        )
        cmd = MemoryCommand(
            bridge=bridge,
            settings=make_settings(),
            db=object(),  # type: ignore[arg-type]
            event_bus=EventBus(),
        )
        out_path = tmp_path / "blocked.json"
        out = await cmd.handle(
            f"export --format json --output {out_path}", make_state()
        )
        assert "test mode" in out.lower() or "blocked" in out.lower()
        assert not out_path.exists()
    finally:
        TestModeGuard.deactivate()
    assert TestModeViolation.__name__ == "TestModeViolation"


async def test_memory_remember_empty_text_returns_usage() -> None:
    _reset_registry()
    bridge = FakeBridge()
    cmd = MemoryCommand(
        bridge=bridge,
        settings=make_settings(),
        db=object(),  # type: ignore[arg-type]
        event_bus=EventBus(),
        promoter=FakePromoter(),  # type: ignore[arg-type]
    )
    out = await cmd.handle("remember", make_state())
    assert bridge.staged == []
    assert "usage" in out.lower() or "remember <text>" in out.lower()


async def test_memory_export_invalid_format_returns_error() -> None:
    _reset_registry()
    bridge = FakeBridge()
    cmd = MemoryCommand(
        bridge=bridge,
        settings=make_settings(),
        db=object(),  # type: ignore[arg-type]
        event_bus=EventBus(),
    )
    out = await cmd.handle("export --format xml", make_state())
    assert "invalid" in out.lower() or "expected json or csv" in out.lower()
