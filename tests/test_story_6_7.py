"""`/memory` over CURATED memory (was Story 6.7, part A).

REWRITTEN, not patched. This file used to test `/memory` against the fact store:
`remember` staged and promoted a fact, `forget` deleted one by id prefix,
`search` returned semantic hits, `export` dumped rows to JSON/CSV, and `menu`
browsed them. That store is gone (D08.1) and the command now operates on the two
curated files, so every one of those tests was asserting a contract that no
longer exists.

The `/staged` half of the original file went with that command entirely.

What the command means now: `stats` and `budget` describe how full your profile
and each owl's notes are, `search` finds entries by substring, `remember` and
`forget` edit YOUR profile under the same budget the agent lives with, and
`export` prints the files verbatim.
"""

from __future__ import annotations

import pytest

from stackowl.commands.memory_command import MemoryCommand
from stackowl.commands.registry import CommandRegistry
from stackowl.memory.curated import USER_TARGET, CuratedMemory
from tests._story_6_7_helpers import make_state

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _isolated_memory(tmp_path, monkeypatch):
    """Point every CuratedMemory the command builds at a temp directory."""
    monkeypatch.setattr(
        MemoryCommand, "_curated",
        lambda self: CuratedMemory(root=tmp_path / "memory"),
    )
    CommandRegistry.reset()
    return CuratedMemory(root=tmp_path / "memory")


def _cmd() -> MemoryCommand:
    return MemoryCommand()


def _text(out: object) -> str:
    return out.text if hasattr(out, "text") else out  # type: ignore[return-value]


# --------------------------------------------------------------------------- #
# remember — writes YOUR profile, under the agent's budget
# --------------------------------------------------------------------------- #


async def test_remember_writes_to_the_user_profile(_isolated_memory):
    out = _text(await _cmd().handle("remember I prefer terse replies", make_state()))

    assert "✓" in out
    assert [e.text for e in _isolated_memory.entries(USER_TARGET)] == [
        "I prefer terse replies",
    ]


async def test_remember_defaults_to_permanent(_isolated_memory):
    """A person writing about themselves is stating something they expect to
    stay true."""
    await _cmd().handle("remember I run everything on a Jetson", make_state())

    assert _isolated_memory.entries(USER_TARGET)[0].durability == "permanent"


async def test_remember_accepts_until_changed(_isolated_memory):
    await _cmd().handle("remember --until-changed I use uv, not npm", make_state())

    entry = _isolated_memory.entries(USER_TARGET)[0]
    assert entry.durability == "until_changed"
    assert entry.text == "I use uv, not npm"


async def test_remember_reports_the_budget(_isolated_memory):
    """The number that actually binds, shown at the moment you spend against it."""
    out = _text(await _cmd().handle("remember something worth keeping", make_state()))

    assert "chars" in out


async def test_remember_can_refuse_the_user_too(_isolated_memory):
    """One curated surface, one budget (R8Q29). It refuses you exactly as it
    refuses the agent — an exemption would mean the agent gets evicted to make
    room for you, silently."""
    mem = _isolated_memory
    i = 0
    while mem.used_chars(USER_TARGET) < mem.budget_for(USER_TARGET) - 80:
        mem.add(USER_TARGET, f"Existing entry number {i} about how I work.", "permanent")
        i += 1

    out = _text(await _cmd().handle("remember " + "x" * 300, make_state()))

    assert "✗" in out


async def test_remember_without_text_shows_usage(_isolated_memory):
    assert "Usage" in _text(await _cmd().handle("remember   ", make_state()))


# --------------------------------------------------------------------------- #
# forget
# --------------------------------------------------------------------------- #


async def test_forget_removes_a_matching_entry(_isolated_memory):
    _isolated_memory.add(USER_TARGET, "Temporary interest in X.", "until_changed")

    out = _text(await _cmd().handle("forget Temporary interest", make_state()))

    assert "✓" in out
    assert _isolated_memory.entries(USER_TARGET) == []


async def test_forget_reports_a_miss(_isolated_memory):
    _isolated_memory.add(USER_TARGET, "Something.", "permanent")

    out = _text(await _cmd().handle("forget nothing like this", make_state()))

    assert "✗" in out
    assert len(_isolated_memory.entries(USER_TARGET)) == 1


async def test_forget_without_text_shows_usage(_isolated_memory):
    assert "Usage" in _text(await _cmd().handle("forget", make_state()))


# --------------------------------------------------------------------------- #
# stats / budget / search / export
# --------------------------------------------------------------------------- #


async def test_stats_counts_entries_per_file(_isolated_memory):
    _isolated_memory.add(USER_TARGET, "About me.", "permanent")
    _isolated_memory.add("scout", "About my job.", "permanent")

    out = _text(await _cmd().handle("stats", make_state()))

    assert "USER.md" in out
    assert "scout.md" in out


async def test_stats_says_so_when_empty(_isolated_memory):
    """Better than a confident '0 facts', which implies a pipeline behind it."""
    assert "empty" in _text(await _cmd().handle("stats", make_state())).lower()


async def test_budget_shows_the_limit_that_binds(_isolated_memory):
    _isolated_memory.add(USER_TARGET, "A thing.", "permanent")

    out = _text(await _cmd().handle("budget", make_state()))

    assert "1,375" in out, "the user profile's hard character budget"


async def test_search_finds_an_entry_by_substring(_isolated_memory):
    _isolated_memory.add(USER_TARGET, "Runs everything on a Jetson.", "permanent")

    out = _text(await _cmd().handle("search jetson", make_state()))

    assert "Jetson" in out


async def test_search_reports_a_miss_plainly(_isolated_memory):
    assert "No curated entries" in _text(await _cmd().handle("search nothing", make_state()))


async def test_search_without_a_query_shows_usage(_isolated_memory):
    assert "Usage" in _text(await _cmd().handle("search", make_state()))


async def test_export_prints_the_file_verbatim(_isolated_memory):
    """Verbatim is the contract: what you read is what the model reads."""
    _isolated_memory.add(USER_TARGET, "Exact text, unreformatted.", "permanent")

    out = _text(await _cmd().handle("export", make_state()))

    assert "Exact text, unreformatted." in out
    assert "USER.md" in out


async def test_export_says_so_when_empty(_isolated_memory):
    assert "empty" in _text(await _cmd().handle("export", make_state())).lower()


# --------------------------------------------------------------------------- #
# The surface itself
# --------------------------------------------------------------------------- #


async def test_reindex_and_menu_are_gone(_isolated_memory):
    """Both operated on the vector index and the fact browser. Neither has
    anything to work on, and a command that reports on nothing implies a
    pipeline that still works."""
    names = {s.name for s in _cmd().meta.subcommands}

    assert "reindex" not in names
    assert "menu" not in names
    assert {"stats", "search", "budget", "remember", "forget", "export"} <= names
