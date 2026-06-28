"""Tests for the ``set_output_preference`` tool.

Closes the write half of the output-preference loop: the deterministic delivery
seam (``apply_output_preferences``) READS ``output_tables`` but nothing ever
WROTE it. This tool is the LLM-driven write path — the model calls it when the
user expresses a format preference, persisting a canonical value GLOBALLY (under
``GLOBAL_OWNER_KEY``) so enforcement fires on every channel.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import TYPE_CHECKING

from stackowl.channels._format import OUTPUT_STYLE_KEY, load_output_style
from stackowl.db.pool import DbPool
from stackowl.memory.preferences import GLOBAL_OWNER_KEY, PreferenceStore
from stackowl.pipeline.services import StepServices, reset_services, set_services
from stackowl.tools.base import ToolManifest
from stackowl.tools.knowledge.output_preference import SetOutputPreferenceTool

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Iterator


@contextmanager
def _services(**kw: object) -> Iterator[None]:
    token = set_services(StepServices(**kw))  # type: ignore[arg-type]
    try:
        yield
    finally:
        reset_services(token)


async def test_set_writes_output_tables_off_globally(tmp_db: DbPool) -> None:
    store = PreferenceStore(db=tmp_db)
    tool = SetOutputPreferenceTool()
    with _services(preference_store=store):
        result = await tool.execute(key="output_tables", value="off")
    assert result.success
    assert result.side_effect_committed is True
    # Written under the GLOBAL sentinel, not a channel owner_key.
    assert await store.get(GLOBAL_OWNER_KEY, "output_tables") == "off"


async def test_set_value_on_re_enables(tmp_db: DbPool) -> None:
    store = PreferenceStore(db=tmp_db)
    tool = SetOutputPreferenceTool()
    with _services(preference_store=store):
        await tool.execute(key="output_tables", value="off")
        result = await tool.execute(key="output_tables", value="on")
    assert result.success
    assert await store.get(GLOBAL_OWNER_KEY, "output_tables") == "on"


async def test_unknown_key_refused_without_write(tmp_db: DbPool) -> None:
    store = PreferenceStore(db=tmp_db)
    tool = SetOutputPreferenceTool()
    with _services(preference_store=store):
        result = await tool.execute(key="font_size", value="off")
    assert not result.success
    # Pre-exec refusal — nothing written, must not trip the give-up floor.
    assert result.side_effect_committed is False
    assert await store.list_for_owner(GLOBAL_OWNER_KEY) == {}


async def test_unknown_value_refused_without_write(tmp_db: DbPool) -> None:
    store = PreferenceStore(db=tmp_db)
    tool = SetOutputPreferenceTool()
    with _services(preference_store=store):
        result = await tool.execute(key="output_tables", value="maybe")
    assert not result.success
    assert result.side_effect_committed is False
    assert await store.list_for_owner(GLOBAL_OWNER_KEY) == {}


async def test_missing_args_structured_error(tmp_db: DbPool) -> None:
    store = PreferenceStore(db=tmp_db)
    tool = SetOutputPreferenceTool()
    with _services(preference_store=store):
        result = await tool.execute(key="output_tables")
    assert not result.success
    assert result.side_effect_committed is False


async def test_store_unavailable_degrades_without_raising() -> None:
    tool = SetOutputPreferenceTool()
    with _services(preference_store=None):
        result = await tool.execute(key="output_tables", value="off")
    assert not result.success
    # Store never reached — no write attempted, no side effect.
    assert result.side_effect_committed is False


def test_manifest_severity_and_group() -> None:
    m: ToolManifest = SetOutputPreferenceTool().manifest
    assert m.action_severity == "write"
    assert m.toolset_group == "knowledge"
    assert m.name == "set_output_preference"


def test_registered_in_with_defaults() -> None:
    from stackowl.tools.registry import ToolRegistry

    reg = ToolRegistry.with_defaults()
    assert isinstance(reg.get("set_output_preference"), SetOutputPreferenceTool)


# --------------------------------------------------------------------------- #
# Structured output_style write path (LS1)                                     #
# --------------------------------------------------------------------------- #
async def test_set_style_field_persists_and_reads_back(tmp_db: DbPool) -> None:
    store = PreferenceStore(db=tmp_db)
    tool = SetOutputPreferenceTool()
    with _services(preference_store=store):
        r1 = await tool.execute(key="markdown", value="minimal")
        r2 = await tool.execute(key="links", value="titles")
    assert r1.success and r2.success
    # Stored as one JSON record of explicitly-set fields under the canonical key.
    raw = await store.get(GLOBAL_OWNER_KEY, OUTPUT_STYLE_KEY)
    assert json.loads(raw) == {"markdown": "minimal", "links": "titles"}
    # Read helper resolves it.
    style = await load_output_style(store, "telegram:1")
    assert style.markdown == "minimal"
    assert style.links == "titles"


async def test_set_output_style_whole_record_json(tmp_db: DbPool) -> None:
    store = PreferenceStore(db=tmp_db)
    tool = SetOutputPreferenceTool()
    with _services(preference_store=store):
        r = await tool.execute(
            key="output_style", value='{"markdown":"minimal","tables":"off"}',
        )
    assert r.success
    style = await load_output_style(store, "local")
    assert style.markdown == "minimal"
    assert style.tables == "off"


async def test_unknown_style_value_refused_without_write(tmp_db: DbPool) -> None:
    store = PreferenceStore(db=tmp_db)
    tool = SetOutputPreferenceTool()
    with _services(preference_store=store):
        result = await tool.execute(key="markdown", value="sparkly")
    assert not result.success
    assert result.side_effect_committed is False
    assert await store.list_for_owner(GLOBAL_OWNER_KEY) == {}


async def test_unknown_style_subkey_in_record_refused(tmp_db: DbPool) -> None:
    store = PreferenceStore(db=tmp_db)
    tool = SetOutputPreferenceTool()
    with _services(preference_store=store):
        result = await tool.execute(key="output_style", value='{"font_size":"big"}')
    assert not result.success
    assert result.side_effect_committed is False
    assert await store.list_for_owner(GLOBAL_OWNER_KEY) == {}


async def test_partial_style_merges_not_resets(tmp_db: DbPool) -> None:
    store = PreferenceStore(db=tmp_db)
    tool = SetOutputPreferenceTool()
    with _services(preference_store=store):
        await tool.execute(key="markdown", value="minimal")
        await tool.execute(key="links", value="titles")  # second field merges in
    raw = await store.get(GLOBAL_OWNER_KEY, OUTPUT_STYLE_KEY)
    assert json.loads(raw) == {"markdown": "minimal", "links": "titles"}
