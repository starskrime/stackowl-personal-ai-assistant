"""Integration test: /owls add persistence via _upsert_to_yaml + catalog wiring."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from stackowl.commands.owls_command import OwlsCommand
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.state import PipelineState


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _state(session: str = "sess-1") -> PipelineState:
    return PipelineState(
        trace_id="trace-1",
        session_id=session,
        input_text="hello",
        channel="cli",
        owl_name="Daria",
        pipeline_step="receive",
    )


@pytest.fixture()
def tmp_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cfg = tmp_path / "stackowl.yaml"
    cfg.write_text(yaml.dump({"owls": []}), encoding="utf-8")
    monkeypatch.setenv("STACKOWL_CONFIG_FILE", str(cfg))
    return cfg


def _load(cfg: Path) -> dict[str, Any]:
    return yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_preset_persists_bounds_to_yaml(tmp_yaml: Path):
    """add with --preset researcher writes bounds into stackowl.yaml."""
    reg = OwlRegistry()
    cmd = OwlsCommand(owl_registry=reg)
    out = await cmd.handle(
        "add rsr --role research --tier fast --preset researcher", _state()
    )
    assert "✓" in out
    owls = _load(tmp_yaml)["owls"]
    entry = next(e for e in owls if e["name"] == "rsr")
    assert "shell" not in entry["bounds"]["tools"]
    assert "delegate_task" in entry["bounds"]["tools"]
    assert reg.get("rsr").bounds is not None


@pytest.mark.asyncio
async def test_upsert_replaces_existing_entry(tmp_yaml: Path):
    """Running add twice on the same name replaces the YAML entry (no duplicates)."""
    reg = OwlRegistry()
    cmd = OwlsCommand(owl_registry=reg)

    # First add
    await cmd.handle(
        "add rsr --role research --tier fast --preset researcher", _state()
    )
    # Manually mutate the registry so the second add doesn't raise duplicate error
    reg.deregister("rsr")
    # Second add with different tier
    await cmd.handle(
        "add rsr --role research --tier standard --preset researcher", _state("sess-2")
    )

    owls = _load(tmp_yaml)["owls"]
    rsr_entries = [e for e in owls if e["name"] == "rsr"]
    assert len(rsr_entries) == 1, "upsert must not create duplicates"
    assert rsr_entries[0]["model_tier"] == "standard"


@pytest.mark.asyncio
async def test_add_without_tool_registry_still_works(tmp_yaml: Path):
    """OwlsCommand with tool_registry=None (default) does not break add."""
    reg = OwlRegistry()
    cmd = OwlsCommand(owl_registry=reg, tool_registry=None)
    out = await cmd.handle(
        "add scout --role helper --tier fast --preset researcher", _state()
    )
    assert "✓" in out
    owls = _load(tmp_yaml)["owls"]
    assert any(e["name"] == "scout" for e in owls)
