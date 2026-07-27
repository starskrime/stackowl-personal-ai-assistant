"""D01.1 slice 3 — the stable user profile that replaces per-turn recall.

Bakir, 2026-07-25 (Q5 + Q12 + Q13, reconciled):

    memory_shape:      "A stable profile of the user, always loaded, PLUS a
                        memory tool the model can call for depth."
    memory_legibility: "Plain file the user can open and edit."
    recall_risk:       "ACCEPTED. Memory is weak today, so a regression is
                        tolerable; the profile + tool is expected to be a net
                        gain. No recall-regression gate required."

Two answers of his could not both hold as stated — "a stable picture of me" and
"I won't give up per-message search" — and surfacing that contradiction is what
produced this design: a loaded profile PLUS an on-demand `memory` tool, which
neither answer contained on its own.

The profile is what makes the prompt freezable. Per-turn recall varied in EVERY
session measured (2026-07-27), making it the single largest source of prompt
instability, and a prompt that changes every turn forfeits the provider's
automatic prefix cache silently.
"""

from __future__ import annotations

from pathlib import Path

from stackowl.memory.user_profile import load_user_profile


def test_the_profile_is_read_from_the_users_own_file(tmp_path: Path) -> None:
    p = tmp_path / "USER.md"
    p.write_text("Bakir builds StackOwl. Prefers root-cause fixes.", encoding="utf-8")

    assert "Bakir builds StackOwl" in load_user_profile(p)


def test_a_missing_profile_is_empty_not_an_error(tmp_path: Path) -> None:
    """Invariant I2 — a part that cannot be resolved degrades to its absence and
    never fails the turn. Most installs will not have written one yet."""
    assert load_user_profile(tmp_path / "nope.md") == ""


def test_an_unreadable_profile_degrades_to_empty(tmp_path: Path) -> None:
    """A directory where a file should be: still the turn's problem to survive,
    not to crash on."""
    d = tmp_path / "USER.md"
    d.mkdir()

    assert load_user_profile(d) == ""


def test_the_profile_is_returned_verbatim(tmp_path: Path) -> None:
    """It is the user's own file — the point is that they can open it and see
    exactly what their assistant is being told about them. Reformatting it here
    would break that contract quietly."""
    body = "# Me\n\n- timezone: CDT\n- hates: hedging\n"
    p = tmp_path / "USER.md"
    p.write_text(body, encoding="utf-8")

    assert load_user_profile(p) == body.strip()


def test_a_whitespace_only_profile_counts_as_absent(tmp_path: Path) -> None:
    """An empty file must not inject a blank region into the prompt — that would
    be an invisible difference between installs that have touched the file and
    those that have not."""
    p = tmp_path / "USER.md"
    p.write_text("   \n\n  \n", encoding="utf-8")

    assert load_user_profile(p) == ""


def test_the_same_file_yields_the_same_text(tmp_path: Path) -> None:
    """The property the whole item rests on: this part must not vary between
    turns, or invariant I1 (one distinct prompt_hash per session) cannot hold."""
    p = tmp_path / "USER.md"
    p.write_text("stable facts about the user", encoding="utf-8")

    assert load_user_profile(p) == load_user_profile(p)
