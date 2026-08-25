"""ESC-48 — a memory write goes where the fact is ABOUT, and says where it went.

BAKIR'S DECISION, 2026-08-24, after rejecting the previous attempt outright ("the
whole approach is wrong — rethink it"):

  * route on the fact's SUBJECT, inferred from the fact text — not on the calling
    owl, and not on an explicit argument the model must remember to pass;
  * on low confidence, fall back to the USER's file;
  * the confirmation must ALWAYS name where it landed.

THE LAST HALF IS THE LOAD-BEARING ONE. Inference is the option whose failure mode
sank the previous attempt: a wrong destination that still answered "Saved.", so a
misroute was invisible. Measured before this change, `CuratedMemory.add` returned

    "Saved. It reaches the system prompt on the next /new — this conversation
     keeps the prompt it started with."

`MemoryResult` carried `target` in its payload, but the sentence the model and the
user actually read never stated it. Naming the destination is what converts a
silent misroute into one Bakir corrects in a sentence, and it is what makes
inference safe enough to use at all.

NO HARDCODED NAME LIST. The candidate targets are read from the LIVE roster — the
`.md` files that exist — which is the same source `CuratedMemory.search` already
enumerates. That keeps it multilingual, keeps it correct when an owl is renamed,
and keeps one copy of "what targets exist".

AND IT MUST NOT WORSEN A COLLISION ALREADY THERE. `~/.stackowl/memory/` currently
holds BOTH `falcon.md` and `Falcon.md`, because `path_for` builds the filename
from the target verbatim and the model spelled it two ways. An inferred target
therefore resolves to the spelling of the file that ALREADY EXISTS rather than to
whatever case appeared in the sentence.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stackowl.memory.curated import USER_TARGET, CuratedMemory


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    """A memory dir with a realistic roster, including the real case collision."""
    d = tmp_path / "memory"
    d.mkdir()
    (d / "USER.md").write_text("[permanent] Bakir prefers root-cause fixes.\n", encoding="utf-8")
    for owl in ("secretary", "jobmarket", "mailbutler", "Falcon"):
        (d / f"{owl}.md").write_text(f"[permanent] {owl} notes.\n", encoding="utf-8")
    return d


@pytest.fixture()
def mem(root: Path) -> CuratedMemory:
    return CuratedMemory(root=root)


# ---------------------------------------------------------------------------
# The confirmation must name its destination — the load-bearing guarantee
# ---------------------------------------------------------------------------

def test_a_successful_add_names_where_it_landed(mem: CuratedMemory) -> None:
    """The defect: "Saved." regardless of destination made a misroute invisible."""
    result = mem.add(USER_TARGET, "Bakir works in Plano.", "permanent")

    assert result.ok
    assert "USER.md" in result.message or "user" in result.message.lower(), (
        f"the confirmation must say where the fact landed — got {result.message!r}"
    )


def test_an_owl_targeted_add_names_the_owl(mem: CuratedMemory) -> None:
    result = mem.add("jobmarket", "Prefers remote roles.", "permanent")

    assert result.ok
    assert "jobmarket" in result.message, (
        f"a write to an owl must name that owl — got {result.message!r}"
    )


def test_the_two_destinations_do_not_read_the_same(mem: CuratedMemory) -> None:
    """The whole point. If both say the same sentence, naming has bought nothing."""
    to_user = mem.add(USER_TARGET, "Fact one.", "permanent").message
    to_owl = mem.add("jobmarket", "Fact two.", "permanent").message

    assert to_user != to_owl


# ---------------------------------------------------------------------------
# Inference: derived from the LIVE roster, never a hardcoded list
# ---------------------------------------------------------------------------

def test_a_fact_naming_an_owl_routes_to_that_owl(mem: CuratedMemory) -> None:
    assert mem.infer_target("jobmarket should only look at remote roles") == "jobmarket"


def test_a_fact_naming_nobody_falls_back_to_the_user(mem: CuratedMemory) -> None:
    """Bakir's explicit choice: low confidence lands on the USER's file."""
    assert mem.infer_target("prefers remote roles") == USER_TARGET


def test_an_AMBIGUOUS_fact_falls_back_rather_than_guessing(mem: CuratedMemory) -> None:
    """Two owls named is not more information, it is less. Guessing here is the
    silent-misroute failure mode wearing a confident face."""
    assert mem.infer_target("have jobmarket send its results to mailbutler") == USER_TARGET


def test_inference_is_case_insensitive_but_resolves_to_the_EXISTING_spelling(
    mem: CuratedMemory, root: Path
) -> None:
    """`falcon.md` and `Falcon.md` both exist on the live box because path_for
    builds the filename verbatim from whatever the model typed. An inferred
    target must land on the file that is already there, not mint a third case."""
    assert mem.infer_target("falcon watches the news feed") == "Falcon"
    assert not (root / "falcon.md").exists(), "must not create a second-case file"


def test_a_substring_is_not_a_match(mem: CuratedMemory) -> None:
    """Whole names only. `jobmarketing` naming `jobmarket` would route a fact
    about a different subject entirely."""
    assert mem.infer_target("jobmarketing is a discipline") == USER_TARGET


def test_the_user_is_never_inferred_away_by_its_own_name(mem: CuratedMemory) -> None:
    assert mem.infer_target("the user prefers dark mode") == USER_TARGET


def test_a_new_owl_is_picked_up_without_a_code_change(mem: CuratedMemory, root: Path) -> None:
    """The roster is READ, not compiled in — an owl created after this test was
    written must route correctly with no edit here."""
    (root / "newsdesk.md").write_text("[permanent] notes.\n", encoding="utf-8")

    assert mem.infer_target("newsdesk should run at 7am") == "newsdesk"


def test_inference_never_raises_on_odd_input(mem: CuratedMemory) -> None:
    for odd in ("", "   ", "…", "1234", "USER.md"):
        assert mem.infer_target(odd) == USER_TARGET
