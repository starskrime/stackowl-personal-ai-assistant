"""Story 4.4 — Owl management slash commands (/owls)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
import yaml

from stackowl.commands.owls_command import OwlsCommand
from stackowl.commands.owls_helpers import (
    format_dna_display,
    format_owl_table,
)
from stackowl.db.pool import DbPool
from stackowl.events.bus import EventBus
from stackowl.exceptions import (
    ManifestValidationError,
    OwlNotFoundError,
)
from stackowl.owls.dna import OwlDNA
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.state import PipelineState


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _state(session: str = "sess-test") -> PipelineState:
    return PipelineState(
        trace_id="trace-test",
        session_id=session,
        input_text="hello",
        channel="cli",
        owl_name="secretary",
        pipeline_step="receive",
    )


def _manifest(name: str, role: str = "analyst", tier: str = "fast") -> OwlAgentManifest:
    return OwlAgentManifest(
        name=name,
        role=role,
        system_prompt=f"You are {name}, a {role}.",
        model_tier=tier,  # type: ignore[arg-type]
    )


@pytest.fixture()
def tmp_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Temporary stackowl.yaml redirected via env var."""
    yaml_path = tmp_path / "stackowl.yaml"
    yaml_path.write_text(yaml.dump({"owls": []}), encoding="utf-8")
    monkeypatch.setenv("STACKOWL_CONFIG_FILE", str(yaml_path))
    return yaml_path


# ---------------------------------------------------------------------------
# OwlRegistry.deregister
# ---------------------------------------------------------------------------


class TestDeregister:
    def test_deregister_secretary_refused(self) -> None:
        reg = OwlRegistry.with_default_secretary()
        with pytest.raises(ManifestValidationError):
            reg.deregister("secretary")

    def test_deregister_unknown_owl(self) -> None:
        reg = OwlRegistry.with_default_secretary()
        with pytest.raises(OwlNotFoundError):
            reg.deregister("ghost")

    def test_deregister_removes_owl(self) -> None:
        reg = OwlRegistry.with_default_secretary()
        reg.register(_manifest("alice"))
        assert any(m.name == "alice" for m in reg.list())
        reg.deregister("alice")
        assert all(m.name != "alice" for m in reg.list())


# ---------------------------------------------------------------------------
# owls_helpers — pure functions
# ---------------------------------------------------------------------------


class TestOwlsHelpers:
    def test_format_owl_table_empty(self) -> None:
        out = format_owl_table([])
        assert "no owls" in out.lower()

    def test_format_owl_table_orders_input(self) -> None:
        manifests = [_manifest("alice"), _manifest("bob"), _manifest("carol")]
        out = format_owl_table(manifests)
        a = out.find("alice")
        b = out.find("bob")
        c = out.find("carol")
        assert 0 < a < b < c

    def test_format_owl_table_uses_ascii_only(self) -> None:
        manifests = [_manifest("alice")]
        out = format_owl_table(manifests)
        # No unicode box-drawing characters allowed.
        for ch in ("┃", "│", "─", "┌", "┐", "└", "┘", "┬", "┴", "┼"):
            assert ch not in out

    def test_format_dna_display_no_db_says_not_yet_persisted(self) -> None:
        out = format_dna_display("alice", OwlDNA(), None)
        assert "alice" in out
        assert "not yet persisted" in out

    def test_format_dna_display_uses_db_values(self) -> None:
        db_row: dict[str, Any] = {
            "challenge_level": 0.72,
            "verbosity": 0.5,
            "curiosity": 0.5,
            "formality": 0.5,
            "creativity": 0.5,
            "precision": 0.5,
            "updated_at": "2026-05-23T00:00:00+00:00",
        }
        out = format_dna_display("alice", OwlDNA(), db_row)
        assert "0.72" in out
        assert "2026-05-23" in out


# ---------------------------------------------------------------------------
# OwlsCommand — subcommands
# ---------------------------------------------------------------------------


class TestOwlsList:
    async def test_owls_list_secretary_only(self) -> None:
        reg = OwlRegistry.with_default_secretary()
        cmd = OwlsCommand(owl_registry=reg)
        out = await cmd.handle("list", _state())
        assert "secretary" in out

    async def test_owls_list_multiple_owls(self) -> None:
        reg = OwlRegistry.with_default_secretary()
        reg.register(_manifest("alice"))
        reg.register(_manifest("bob"))
        cmd = OwlsCommand(owl_registry=reg)
        out = await cmd.handle("list", _state())
        # All three should appear (alphabetically: alice, bob, secretary).
        for name in ("alice", "bob", "secretary"):
            assert name in out

    async def test_owls_list_no_registry(self) -> None:
        cmd = OwlsCommand(owl_registry=None)
        out = await cmd.handle("list", _state())
        assert "no owl registry" in out.lower()

    async def test_owls_default_subcommand_is_list(self) -> None:
        reg = OwlRegistry.with_default_secretary()
        cmd = OwlsCommand(owl_registry=reg)
        out = await cmd.handle("", _state())
        assert "secretary" in out


class TestOwlsDna:
    async def test_owls_dna_shows_traits_without_db(self) -> None:
        reg = OwlRegistry.with_default_secretary()
        reg.register(
            OwlAgentManifest(
                name="alice",
                role="analyst",
                system_prompt="be alice",
                model_tier="fast",
                dna=OwlDNA(challenge_level=0.8),
            )
        )
        cmd = OwlsCommand(owl_registry=reg, db=None)
        out = await cmd.handle("dna alice", _state())
        assert "0.80" in out
        assert "not yet persisted" in out

    async def test_owls_dna_reads_db_row(self, tmp_db: DbPool) -> None:
        reg = OwlRegistry.with_default_secretary()
        reg.register(_manifest("alice"))
        await tmp_db.execute(
            "INSERT INTO owl_dna (owl_name, challenge_level, verbosity, curiosity, "
            "formality, creativity, precision, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("alice", 0.91, 0.5, 0.5, 0.5, 0.5, 0.5, "2026-05-23T00:00:00+00:00"),
        )
        cmd = OwlsCommand(owl_registry=reg, db=tmp_db)
        out = await cmd.handle("dna alice", _state())
        assert "0.91" in out
        assert "2026-05-23" in out

    async def test_owls_dna_unknown_owl(self) -> None:
        reg = OwlRegistry.with_default_secretary()
        cmd = OwlsCommand(owl_registry=reg)
        out = await cmd.handle("dna ghost", _state())
        assert "✗" in out
        assert "ghost" in out.lower()

    async def test_owls_dna_missing_name(self) -> None:
        reg = OwlRegistry.with_default_secretary()
        cmd = OwlsCommand(owl_registry=reg)
        out = await cmd.handle("dna", _state())
        assert "Usage:" in out


class TestOwlsHealth:
    async def test_owls_health_ok_with_secretary(self) -> None:
        reg = OwlRegistry.with_default_secretary()
        cmd = OwlsCommand(owl_registry=reg)
        out = await cmd.handle("health", _state())
        assert "ok" in out
        assert "healthy" in out.lower()

    async def test_owls_health_down_without_secretary(self) -> None:
        reg = OwlRegistry()  # no secretary registered
        cmd = OwlsCommand(owl_registry=reg)
        out = await cmd.handle("health", _state())
        assert "down" in out
        assert "Secretary" in out


class TestOwlsRemove:
    async def test_owls_remove_requires_yes(self, tmp_yaml: Path) -> None:
        reg = OwlRegistry.with_default_secretary()
        reg.register(_manifest("alice"))
        cmd = OwlsCommand(owl_registry=reg)
        out = await cmd.handle("remove alice", _state())
        assert "YES" in out
        # Owl should still be present — not yet confirmed.
        assert reg.get("alice").name == "alice"

    async def test_owls_remove_with_yes(self, tmp_yaml: Path) -> None:
        reg = OwlRegistry.with_default_secretary()
        reg.register(_manifest("alice"))
        # Seed YAML with alice so we can prove removal of YAML entry.
        tmp_yaml.write_text(
            yaml.dump({"owls": [{"name": "alice", "role": "analyst"}]}),
            encoding="utf-8",
        )
        cmd = OwlsCommand(owl_registry=reg)
        out = await cmd.handle("remove alice YES", _state())
        assert "✓" in out
        with pytest.raises(OwlNotFoundError):
            reg.get("alice")
        data = yaml.safe_load(tmp_yaml.read_text(encoding="utf-8"))
        names = [entry["name"] for entry in data.get("owls", [])]
        assert "alice" not in names

    async def test_owls_remove_secretary_refused(self, tmp_yaml: Path) -> None:
        reg = OwlRegistry.with_default_secretary()
        cmd = OwlsCommand(owl_registry=reg)
        out = await cmd.handle("remove secretary YES", _state())
        assert "✗" in out
        assert reg.has_secretary()

    async def test_owls_remove_unknown_owl(self, tmp_yaml: Path) -> None:
        reg = OwlRegistry.with_default_secretary()
        cmd = OwlsCommand(owl_registry=reg)
        out = await cmd.handle("remove ghost YES", _state())
        assert "✗" in out
        assert "ghost" in out.lower()

    async def test_owls_remove_deletes_dna_rows(self, tmp_yaml: Path) -> None:
        reg = OwlRegistry.with_default_secretary()
        reg.register(_manifest("alice"))
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=None)
        cmd = OwlsCommand(owl_registry=reg, db=mock_db)
        await cmd.handle("remove alice YES", _state())
        called_sqls = [call.args[0] for call in mock_db.execute.await_args_list]
        assert any("DELETE FROM owl_dna" in sql for sql in called_sqls)
        assert any("DELETE FROM dna_checkpoints" in sql for sql in called_sqls)

    async def test_owls_remove_emits_event(self, tmp_yaml: Path) -> None:
        reg = OwlRegistry.with_default_secretary()
        reg.register(_manifest("alice"))
        bus = EventBus()
        captured: list[Any] = []
        bus.subscribe("owl_removed", lambda payload: captured.append(payload))
        cmd = OwlsCommand(owl_registry=reg, event_bus=bus)
        await cmd.handle("remove alice YES", _state())
        assert captured and captured[0]["name"] == "alice"

    async def test_owls_remove_no_args(self, tmp_yaml: Path) -> None:
        reg = OwlRegistry.with_default_secretary()
        cmd = OwlsCommand(owl_registry=reg)
        out = await cmd.handle("remove", _state())
        assert "Usage:" in out


# ---------------------------------------------------------------------------
# Smoke — ensure a timestamp shape is at least parseable.
# ---------------------------------------------------------------------------


def test_iso_timestamp_round_trip() -> None:
    ts = datetime.now(UTC).isoformat()
    parsed = datetime.fromisoformat(ts)
    assert parsed.tzinfo is not None
