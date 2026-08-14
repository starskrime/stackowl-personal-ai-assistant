"""Curated substring search lives on CuratedMemory, and both tools ask it.

ESC-3. Bakir chose to KEEP `browser_recall_url` and repoint it at curated
memory rather than remove it. That needs a curated substring search — and the
`memory` tool already had one, as a private `_curated_matches` method.

Copying it into the browser tool would have been two copies of one rule, the
shape this programme keeps finding and fixing. So the search moves to
`CuratedMemory.search()`, its actual home: it is a property of curated memory,
not of whichever tool happens to want it. One source; both tools ask it.

WHAT THE REPOINTED TOOL HONESTLY ANSWERS. Curated memory is NOT a page cache —
it is two small text files under a hard character budget, holding what the agent
chose to remember about the user and its own work. So `browser_recall_url` can
no longer mean "here is the page I fetched earlier"; it means "have I written
anything down about this URL". That is a narrower promise than the tool made
when it read `committed_facts`, and the tool says so rather than implying the
page content was retained.
"""

from __future__ import annotations

import json
from pathlib import Path


def _curated(tmp_path: Path):
    from stackowl.memory.curated import CuratedMemory

    return CuratedMemory(root=tmp_path)


def test_search_finds_an_entry_in_the_user_profile(tmp_path: Path) -> None:
    from stackowl.memory.curated import USER_TARGET

    mem = _curated(tmp_path)
    mem.add(USER_TARGET, "Bakir's dashboard lives at grafana.internal", "permanent")

    hits = mem.search("grafana.internal")

    assert [t for t, _ in hits] == [USER_TARGET]
    assert "grafana.internal" in hits[0][1]


def test_search_is_case_insensitive(tmp_path: Path) -> None:
    from stackowl.memory.curated import USER_TARGET

    mem = _curated(tmp_path)
    mem.add(USER_TARGET, "The Runbook is at EXAMPLE.COM/ops", "permanent")

    assert mem.search("example.com/ops"), "search must fold case"


def test_search_spans_owl_files_not_just_the_user_profile(tmp_path: Path) -> None:
    mem = _curated(tmp_path)
    mem.add("secretary", "Telegram replies stay under 2048 tokens", "until_changed")

    hits = mem.search("2048 tokens")

    assert [t for t, _ in hits] == ["secretary"], hits


def test_search_returns_empty_rather_than_raising_on_a_missing_directory(
    tmp_path: Path,
) -> None:
    """A search must never cost the caller an exception — every call site treats
    an empty result as 'nothing known', which is the honest answer."""
    mem = _curated(tmp_path / "does-not-exist")
    assert mem.search("anything") == []


def test_the_memory_tool_no_longer_carries_its_own_copy() -> None:
    """The point of the move: one source, not two implementations that drift."""
    import inspect

    from stackowl.tools.knowledge import memory as memory_tool

    src = inspect.getsource(memory_tool)
    assert "def _curated_matches" not in src, (
        "the private copy is still in the memory tool — both callers must go "
        "through CuratedMemory.search"
    )


# ---------------------------------------------------------------------------
# browser_recall_url, repointed (ESC-3)
# ---------------------------------------------------------------------------


import pytest  # noqa: E402


@pytest.mark.asyncio
async def test_browser_recall_url_finds_a_url_written_in_curated_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from stackowl.memory.curated import USER_TARGET
    from stackowl.tools.browser.tools import BrowserRecallUrlTool

    mem = _curated(tmp_path)
    mem.add(USER_TARGET, "The ops runbook is at example.com/ops", "permanent")
    monkeypatch.setattr("stackowl.memory.curated.shared_memory", lambda: mem)

    result = await BrowserRecallUrlTool().execute(url="https://example.com/ops")

    # ToolResult.output is a JSON STRING, not a dict — the shape the real tool
    # layer returns, so the test reads it the way production does.
    payload = json.loads(result.output)
    assert payload["found"] is True, payload
    assert "runbook" in str(payload.get("notes", "")).lower()


@pytest.mark.asyncio
async def test_browser_recall_url_reports_not_found_when_nothing_was_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The honest answer, and the ONLY one this tool could give before ESC-3 —
    it read committed_facts, empty since migration 0112."""
    from stackowl.tools.browser.tools import BrowserRecallUrlTool

    monkeypatch.setattr(
        "stackowl.memory.curated.shared_memory", lambda: _curated(tmp_path)
    )

    result = await BrowserRecallUrlTool().execute(url="https://nobody.mentioned.this")

    payload = json.loads(result.output)
    assert payload["found"] is False, payload
