"""Integration test: /owls edit persistence via _upsert_to_yaml + catalog wiring.

(``add`` was retired in Task 7 — owls are seeded directly through
:class:`SpecialistOwlBuilder`, the same one constructor ``add`` used to
delegate to, instead of going through the deleted subcommand.)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from stackowl.commands.owls_command import OwlsCommand
from stackowl.commands.owls_helpers import manifest_to_yaml_entry
from stackowl.owls.builder import OwlSpec, SpecialistOwlBuilder
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


def _seed_bounded(reg: OwlRegistry, name: str = "rsr", tier: str = "fast") -> None:
    """Register+persist a preset-bounded owl directly through the one builder,
    the same construction path the deleted ``/owls add`` used to delegate to."""
    manifest = SpecialistOwlBuilder().build(
        OwlSpec(name=name, role="research", model_tier=tier, preset="researcher")
    )
    reg.register(manifest)
    OwlsCommand(owl_registry=reg)._upsert_to_yaml(manifest_to_yaml_entry(manifest))


# ---------------------------------------------------------------------------
# /owls edit tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_edit_changes_field_and_repersists(tmp_yaml: Path):
    reg = OwlRegistry()
    cmd = OwlsCommand(owl_registry=reg)
    _seed_bounded(reg)
    out = await cmd.handle("edit rsr --tier powerful", _state())
    assert "✓" in out
    assert reg.get("rsr").model_tier == "powerful"
    assert reg.get("rsr").bounds is not None and "delegate_task" in reg.get("rsr").bounds.tools
    entry = next(e for e in _load(tmp_yaml)["owls"] if e["name"] == "rsr")
    assert entry["model_tier"] == "powerful"


@pytest.mark.asyncio
async def test_edit_secretary_rejected(tmp_yaml: Path):
    reg = OwlRegistry.with_default_secretary()
    cmd = OwlsCommand(owl_registry=reg)
    from stackowl.owls.registry import _SECRETARY_NAME
    out = await cmd.handle(f"edit {_SECRETARY_NAME} --tier fast", _state())
    assert "✗" in out


@pytest.mark.asyncio
async def test_edit_unknown_owl_errors(tmp_yaml: Path):
    out = await OwlsCommand(owl_registry=OwlRegistry()).handle("edit ghost --tier fast", _state())
    assert "✗" in out


@pytest.mark.asyncio
async def test_edit_with_no_fields_is_rejected(tmp_yaml: Path):
    reg = OwlRegistry()
    cmd = OwlsCommand(owl_registry=reg)
    _seed_bounded(reg)
    out = await cmd.handle("edit rsr", _state())
    assert "✗" in out  # no silent no-op success / needless yaml rewrite
