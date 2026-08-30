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
from tests._schema_template import seed_schema

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _state(session: str = "sess-1") -> PipelineState:
    return PipelineState(
        trace_id="trace-1",
        session_key=session,
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
async def test_edit_changes_field_and_repersists(tmp_yaml: Path, tmp_path: Path):
    """STORAGE MOVED 2026-08-16 (migration 0118): an owl's durable home is the
    `owls` table, not stackowl.yaml, so "repersists" is now checked against the
    store. The edit is wired to a REAL db here rather than dropping the durability
    half of this test — an edit that changes memory and persists nothing is
    exactly the bug this arc came out of.
    """
    from stackowl.db.pool import DbPool
    from stackowl.owls.store import OwlStore
    from stackowl.pipeline.services import (
        StepServices,
        reset_services,
        set_services,
    )

    db = DbPool(db_path=tmp_path / "owls.db")
    await db.open()
    seed_schema(tmp_path / "owls.db")
    reg = OwlRegistry()
    cmd = OwlsCommand(owl_registry=reg)
    _seed_bounded(reg)
    # Capture what the preset ACTUALLY granted, from the same builder the seed
    # used. See the note below: naming a member here is what made this test rot.
    bounds_before = reg.get("rsr").bounds
    assert bounds_before is not None, "the seed must produce a bounded owl"
    tools_before = frozenset(bounds_before.tools or ())

    token = set_services(StepServices(owl_registry=reg, db_pool=db))
    try:
        out = await cmd.handle("edit rsr --tier powerful", _state())
    finally:
        reset_services(token)

    assert "✓" in out
    assert reg.get("rsr").model_tier == "powerful"
    # THE INVARIANT: editing ONE field must not disturb the owl's authority.
    #
    # This line used to read `"delegate_task" in ... bounds.tools`, and it went
    # red when commit 7a6ed508 DELIBERATELY removed delegate_task from the
    # researcher preset — "the bounds gate granted delegate_task so a blocked owl
    # could route around a limit; the envelope then refused it as off-plan. Two
    # gates behaving exactly as designed, with contradictory designs."
    #
    # So the code was right and the test was stale. The deeper fault is that it
    # named a MEMBER of a preset it does not own: any future preset change breaks
    # it again, and the obvious way to make it pass is to put delegate_task back —
    # re-opening a contradiction someone closed on purpose. Comparing before and
    # after tests the property this test is actually named for, and cannot rot
    # when the preset changes.
    bounds_after = reg.get("rsr").bounds
    assert bounds_after is not None, "the edit must not strip the owl's bounds"
    assert frozenset(bounds_after.tools or ()) == tools_before, (
        "a --tier edit changed the owl's tool authority: "
        f"gained {sorted(frozenset(bounds_after.tools or ()) - tools_before)}, "
        f"lost {sorted(tools_before - frozenset(bounds_after.tools or ()))}"
    )
    persisted = {m.name: m for m in await OwlStore(db).list_all()}
    assert "rsr" in persisted, f"the edit was not persisted: {sorted(persisted)}"
    assert persisted["rsr"].model_tier == "powerful"
    await db.close()


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
