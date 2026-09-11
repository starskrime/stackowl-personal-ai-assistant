"""The one command the loop runs before building answered "the ground is clear" to everything.

`item-loop/SKILL.md` opens every item with:

    uv run python scripts/map_check.py "<what you are about to build>"

MEASURED 2026-09-11: that form matched NOTHING, ever. `main()` built its search
terms from `argv` elements rather than from words, so a quoted sentence became a
single term and `"the whole sentence" in haystack` is essentially never true.
`map_check.py skill curator decay` returned ten matches; `map_check.py "skill
curator decay"` returned "The ground is clear." The tool exists because D09.3 was
designed, built, tested and shipped before anyone noticed the map already had it —
and for every loop that followed the documented invocation, it has been answering
all-clear regardless of what the map held.

Its own docstring carried both forms as if they were equivalent, which is why
nobody looked: the usage block shows bare words on one line and a quoted phrase on
the next.

AND THE FIRST DEFECT WAS HIDING A SECOND. `N01` carries `wave: None` and has since
it was added. The result sort keyed on `item["wave"]` directly, so any result set
containing N01 alongside another item raised
`TypeError: '<' not supported between instances of 'NoneType' and 'int'`. That
crash could not fire while the search matched nothing. Fixing the argv bug exposed
it immediately, on the first realistic query.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "scripts"))

import map_check  # noqa: E402


def _ids(argv: list[str]) -> list[str]:
    """The ids map_check ACTUALLY reports for this argv.

    Calls `map_check.search`, the same path the command runs. The first version of
    this helper re-implemented the term-splitting here, so reverting the real
    splitting changed nothing these tests could observe and BOTH mutations passed.
    A guard that re-implements the thing it guards is testing itself.
    """
    return sorted(i["id"] for _score, i in map_check.search(argv))


@pytest.mark.tripwire
def test_a_quoted_phrase_finds_what_its_bare_words_find() -> None:
    """THE DEFECT. The loop is instructed to pass one quoted string; a human at a
    prompt types bare words. Both must reach the same items, or the documented
    invocation is a no-op that reads like a clean result."""
    phrase = _ids(["skill curator decay"])
    words = _ids(["skill", "curator", "decay"])

    assert phrase == words, (
        "a quoted phrase and its own words disagree — the form SKILL.md tells "
        f"every loop to use returns {len(phrase)} item(s) and the bare-word form "
        f"returns {len(words)}"
    )
    assert phrase, "neither form matched anything — this control has gone blind"


@pytest.mark.tripwire
def test_the_sort_survives_an_item_with_no_wave() -> None:
    """The latent crash the argv bug was hiding. Exercised through `main()` so the
    sort actually runs, rather than through the scorer alone."""
    import yaml

    data = yaml.safe_load((_ROOT / "progress.yml").read_text(encoding="utf-8"))
    waveless = [i["id"] for i in data["items"] if not isinstance(i.get("wave"), int)]
    assert waveless, (
        "no item lacks a wave any more, so this guard no longer exercises the "
        "crash it was written for — re-read it rather than deleting it"
    )

    # The set must be MIXED — a waveless item AND a waved one — or the sort never
    # compares None to an int and the guard passes on a tree that would crash.
    # Both terms are DERIVED, so this keeps working as the map changes.
    waveless_item = next(i for i in data["items"] if i["id"] == waveless[0])
    waved_item = next(i for i in data["items"] if isinstance(i.get("wave"), int))
    query = [waveless_item["title"].split()[0], waved_item["title"].split()[0]]

    found = _ids(query)  # raises TypeError on the unfixed sort

    assert waveless[0] in found, f"{query} no longer reaches the waveless item"
    assert len(found) >= 2, (
        f"{query} returns only {found} — not a mixed set, so this guard would "
        "pass without ever exercising the crash it was written for"
    )


@pytest.mark.tripwire
def test_the_agentic_os_items_are_reachable_by_the_words_a_builder_would_type() -> None:
    """The programme the loop now works is only as discoverable as its vocabulary.

    An item nobody's phrasing reaches is the same as an item that is not there —
    which is the exact failure this script was built to prevent, one level up.
    """
    for phrase, expected in (
        ("what is each agent doing now", "A05.6"),
        ("install a skill from the hub", "A05.8"),
        ("enforce network bounds", "A01.1"),
        ("dashboard for agents and schedules", "A05.3"),
    ):
        found = _ids([phrase])
        assert expected in found, (
            f"{expected!r} is unreachable by {phrase!r} — a builder searching that "
            f"phrase would be told the ground is clear. Got: {found[:6]}"
        )


@pytest.mark.tripwire
def test_the_skill_still_tells_the_loop_to_run_it() -> None:
    """Built-but-not-wired, applied to the guard itself. This whole rule is worth
    nothing if the loop stops invoking the script."""
    skill = (_ROOT / ".claude" / "skills" / "item-loop" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert "scripts/map_check.py" in skill, (
        "nothing invokes map_check.py — the duplicate-work check it exists to be "
        "would never run"
    )


@pytest.mark.tripwire
def test_a_word_matching_most_of_the_map_does_not_drown_the_ones_that_matter() -> None:
    """The regression the argv fix traded for, and its cure.

    Splitting an argv element into WORDS fixed a real defect — a quoted phrase had
    matched nothing, ever. It traded it for the opposite one. MEASURED 2026-09-11:
    "warn when the webhook receiver is bound to loopback but has configured sources"
    returned **81** items at a top score of 4, and three of those four hits were
    `the` (50 of 137 items), `is` (48) and `to` (44), while the terms carrying the
    meaning matched almost nothing — `webhook` 1, `configured` 1, `loopback` 0.

    THE CUT IS RELATIVE, NOT A CONSTANT. A term is dropped when its IDF is below the
    mean IDF of the query's own matching terms. The first attempt used a fixed "more
    than half the map" and did nothing at all, because `the` matches 36% — a number
    that sounds principled is not one. A hard-coded stopword list is banned here for
    a separate and better reason: it is wrong the first time someone searches in
    another language.
    """
    noisy = "warn when the webhook receiver is bound to loopback but has configured sources"
    found = _ids([noisy])

    assert len(found) <= 20, (
        f"{len(found)} items match a natural-language query — the common words are "
        "drowning the specific ones, which is the same as matching nothing"
    )

    # The founding example from map_check's own docstring must still come top.
    ranked = map_check.search(["skill curator decay"])
    assert ranked, "the founding example matches nothing at all"
    assert ranked[0][1]["id"] == "D09.3", (
        "D09.3 'Skill curator' is the item this whole script exists to have found; "
        f"it is no longer the top match — got {ranked[0][1]['id']}"
    )


@pytest.mark.tripwire
def test_a_vague_query_still_answers_rather_than_going_silent() -> None:
    """Never return nothing because every term was common. Silence reads as "the
    ground is clear", which is the false all-clear this file's first rule exists to
    prevent.

    TWO PROPERTIES, and neither needs a defensive branch. A single term skips the
    filter entirely — there is nothing to compare it against — and a multi-word query
    of common words still keeps whichever sit at or above the mean, because the mean
    of a set always has a member at or above it. A guard for "everything was dropped"
    was written and deleted: mutation testing removed it and nothing failed.
    """
    assert _ids(["the"]), (
        "a single common word now matches nothing — the filter has turned a noisy "
        "answer into a false all-clear, which is worse"
    )
    assert _ids(["the is to"]), (
        "a query of nothing but common words went silent — the cut is the MEAN of "
        "those terms' IDFs, so at least one must survive it"
    )
