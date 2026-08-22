"""D08.1 slice 3 — the prompt actually carries the curated files.

THE CHECK D01.1 MISSED. That item shipped `load_user_profile()` into every
prompt build against a file that did not exist and that nothing wrote, and no
test noticed, because the tests asserted the code path rather than the prompt.
So these assert on the assembled text.

Replaces tests/memory/test_user_profile.py, whose module was deleted: it tested
a reader whose whole job CuratedMemory now does, and leaving it would have meant
keeping the module alive purely to keep its tests passing.
"""

from __future__ import annotations

import pytest

from stackowl.memory.curated import USER_TARGET, CuratedMemory


@pytest.fixture
def mem(tmp_path):
    return CuratedMemory(root=tmp_path / "memory")


def test_the_user_profile_reaches_the_snapshot(mem):
    mem.add(USER_TARGET, "Bakir builds StackOwl.", "permanent")

    assert "Bakir builds StackOwl." in mem.snapshot_for_prompt(
        USER_TARGET, conversation_id="s1",
    )


def test_owl_notes_are_separate_from_the_profile_in_the_prompt(mem):
    """Two blocks, not one: 'who my user is' and 'what I learned doing my job'
    are different claims and must not be presented as one."""
    mem.add(USER_TARGET, "Prefers root-cause fixes.", "permanent")
    mem.add("scout", "Recovery lanes need a retry budget.", "permanent")

    user = mem.snapshot_for_prompt(USER_TARGET, conversation_id="s1")
    owl = mem.snapshot_for_prompt("scout", conversation_id="s1")

    assert "root-cause" in user and "retry budget" not in user
    assert "retry budget" in owl and "root-cause" not in owl


def test_a_missing_file_contributes_nothing_rather_than_failing(mem):
    """Most installs have never written one, so absence is the ORDINARY case."""
    assert mem.snapshot_for_prompt(USER_TARGET, conversation_id="s1") == ""
    assert mem.snapshot_for_prompt("nobody", conversation_id="s1") == ""


def test_an_unreadable_file_degrades_to_empty(mem, tmp_path, monkeypatch):
    """A profile that cannot be read must cost context, never a reply."""
    mem.add(USER_TARGET, "Something.", "permanent")

    def _boom(*a, **k):
        raise OSError("permission denied")

    monkeypatch.setattr("pathlib.Path.read_text", _boom)

    assert mem.snapshot_for_prompt(USER_TARGET, conversation_id="fresh") == ""


def test_the_entry_text_is_returned_verbatim(mem):
    """The user can open the file and correct it, so what they wrote is what the
    model is told — reformatting would quietly break that contract."""
    text = "Runs on a Jetson; never pull models locally."
    mem.add(USER_TARGET, text, "permanent")

    assert text in mem.snapshot_for_prompt(USER_TARGET, conversation_id="s1")
