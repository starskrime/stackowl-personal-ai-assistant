"""Tests for the ``set_output_preference`` tool.

Closes the write half of the output-preference loop: the deterministic delivery
seam (``apply_output_preferences``) READS ``output_tables`` but nothing ever
WROTE it. This tool is the LLM-driven write path — the model calls it when the
user expresses a format preference, persisting a canonical value GLOBALLY (under
``GLOBAL_OWNER_KEY``) so enforcement fires on every channel.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

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
