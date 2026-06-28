"""LS5 — ``/style`` shows the active enforced output style in plain language.

Read-only command: it reuses the SAME resolver (:func:`load_output_style`) the
delivery seam enforces and the SAME wording (:meth:`OutputStyle.describe_rules`)
the feedback confirmation reads back, so what /style reports cannot drift from
what is enforced. Tests assert on the real store + the rendered string, plus
reachability (the command is registered + manifested).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from stackowl.channels._format import OUTPUT_STYLE_KEY
from stackowl.commands.assembly import CommandDeps, register_all_commands
from stackowl.commands.manifest import SHIPPED_COMMANDS
from stackowl.commands.registry import CommandRegistry
from stackowl.commands.style_command import StyleCommand
from stackowl.db.migrations.runner import MigrationRunner
from stackowl.db.pool import DbPool
from stackowl.memory.preferences import PreferenceStore
from stackowl.pipeline.state import PipelineState


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    CommandRegistry.reset()


@pytest.fixture()
async def store(tmp_path: Path) -> PreferenceStore:
    db_path = tmp_path / "style_test.db"
    MigrationRunner(db_path=db_path).run()
    pool = DbPool(db_path=db_path)
    await pool.open()
    yield PreferenceStore(pool)
    await pool.close()


def _state() -> PipelineState:
    return PipelineState(
        trace_id="t", session_id="s", input_text="", channel="telegram",
        owl_name="secretary", pipeline_step="", identity_key="user1",
    )


async def test_style_with_stored_style_lists_active_rules(store: PreferenceStore) -> None:
    """(a) A stored style renders the active rules in plain language."""
    await store.set("user1", OUTPUT_STYLE_KEY,
                    json.dumps({"markdown": "minimal", "links": "titles"}))
    out = await StyleCommand(preference_store=store).handle("", _state())
    assert "no asterisks" in out
    assert "links shown as titles" in out
    assert "Telegram" in out  # channel surfaced
    assert "/style" in out


async def test_style_with_no_style_is_honest(store: PreferenceStore) -> None:
    """(b) No style set → an honest 'none set' message, not a fabricated rule."""
    out = await StyleCommand(preference_store=store).handle("", _state())
    assert "no custom output style" in out.lower()
    assert "(active)" not in out  # never claims an active rule when none is set


async def test_style_unconfigured_store_is_honest() -> None:
    """A missing store degrades to an honest 'not configured' message."""
    out = await StyleCommand(preference_store=None).handle("", _state())
    assert "not configured" in out.lower()


def test_style_is_registered_and_shipped() -> None:
    """(c) /style is in SHIPPED_COMMANDS and reachable via the spine."""
    assert "style" in SHIPPED_COMMANDS
    CommandRegistry.reset()
    register_all_commands(CommandDeps())
    live = {c.command for c in CommandRegistry.instance().list()}
    assert "style" in live
