"""Functional tests for /preferences (PreferencesCommand) — FR-2.

Covers listing and removing learned content/tone/length preference notes.
Mirrors ``tests/commands/test_connect_command.py``'s shape: direct
``cmd.handle(args, state)`` calls against a fake dependency, no DI/registry
plumbing.
"""

from __future__ import annotations

import pytest

from stackowl.commands.preferences_command import PreferencesCommand
from stackowl.memory.preferences import write_preference_note
from stackowl.pipeline.state import PipelineState

pytestmark = pytest.mark.asyncio

OWNER = "telegram:42"


class FakeStore:
    """In-memory PreferenceStore double keyed by (owner_key, key)."""

    def __init__(self) -> None:
        self.data: dict[tuple[str, str], str] = {}

    async def get(self, owner_key: str, key: str) -> str | None:
        return self.data.get((owner_key, key))

    async def set(self, owner_key: str, key: str, value: str) -> None:
        self.data[(owner_key, key)] = value

    async def list_for_owner(self, owner_key: str) -> dict[str, str]:
        return {k[1]: v for k, v in self.data.items() if k[0] == owner_key}


def _state() -> PipelineState:
    return PipelineState(
        trace_id="t", session_id="s", input_text="", channel="telegram",
        owl_name="secretary", pipeline_step="", identity_key=OWNER,
    )


async def test_list_with_no_notes_is_honest() -> None:
    store = FakeStore()
    out = await PreferencesCommand(preference_store=store).handle("", _state())
    assert "no learned preference notes" in out.lower()


async def test_list_shows_stored_notes() -> None:
    store = FakeStore()
    await write_preference_note(store, OWNER, aspect="length", polarity="negative",
                                text="be more concise")
    out = await PreferencesCommand(preference_store=store).handle("list", _state())
    assert "be more concise" in out
    assert "length" in out
    assert "negative" in out
    assert "1." in out


async def test_remove_deletes_the_note() -> None:
    store = FakeStore()
    await write_preference_note(store, OWNER, aspect="length", polarity="negative",
                                text="be more concise")
    cmd = PreferencesCommand(preference_store=store)
    out = await cmd.handle("remove 1", _state())
    assert "removed" in out.lower()
    listing = await cmd.handle("list", _state())
    assert "be more concise" not in listing
    assert "no learned preference notes" in listing.lower()


async def test_remove_out_of_range_is_honest() -> None:
    store = FakeStore()
    await write_preference_note(store, OWNER, aspect="length", polarity="negative", text="x")
    out = await PreferencesCommand(preference_store=store).handle("remove 9", _state())
    assert "no preference note #9" in out.lower()


async def test_remove_non_numeric_shows_usage() -> None:
    store = FakeStore()
    out = await PreferencesCommand(preference_store=store).handle("remove abc", _state())
    assert "usage" in out.lower()


async def test_unconfigured_store_is_honest() -> None:
    out = await PreferencesCommand(preference_store=None).handle("", _state())
    assert "not configured" in out.lower()
