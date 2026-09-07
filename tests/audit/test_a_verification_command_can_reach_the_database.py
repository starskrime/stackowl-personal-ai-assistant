"""A Verification command must be able to reach the database it asks about.

MEASURED 2026-09-07. Two facts about this deployment were established long ago and
written into `docs/reference-mapping/PROCESS.md` — inside the section called *"Evidence,
not assertion"*, as the worked example of a document that would fail its own Verification:

* the ``sqlite3`` CLI is not installed here, and this tree queries SQLite through the
  module everywhere in ``src/``;
* the live database is ``<workspace>/stackowl.db``, while ``~/.stackowl/stackowl.db`` is
  a ZERO-BYTE stray from 2026-07-25 that still sits where the database looks like it
  should be.

Three days after that section was last edited, ELEVEN Verification commands across six
design documents still invoked ``sqlite3``, and ESC-73's acceptance check named both wrong
things at once — ``sqlite3 stackowl.db`` — so it could not have closed on any database
content since it was written on 2026-08-31.

WHY IT SURVIVED BEING WRITTEN DOWN. The failure is silent by construction: ``command not
found`` goes to stderr and nothing goes to stdout, so a check shaped ``… | wc -l`` reads
**0**, and 0 reads as *not yet* rather than *wrong instrument*. The stray path produces the
identical shape from the other side — a real file, a successful open, and no rows in it,
ever. Neither ever raised its hand.

So the rule stops being prose in the method document and becomes this. It is the fourth
instance of one cure: ``premise_check`` for an escalation's premise, ``closing_check`` for
a partial stage's evidence, ``doc_check``'s staleness header for a document's sources, and
now this for a document's ability to run its own query.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.doc_check import _cannot_query_the_database

_ROOT = Path(__file__).resolve().parents[2]
_DESIGNS = _ROOT / "docs" / "reference-mapping" / "designs"


@pytest.mark.tripwire
def test_no_design_document_queries_a_database_it_cannot_reach() -> None:
    offenders: list[str] = []
    for doc in sorted(_DESIGNS.glob("*.md")):
        for ln, cmd, why in _cannot_query_the_database(doc.read_text(encoding="utf-8")):
            offenders.append(f"{doc.name}:{ln}  {cmd[:90]}\n      {why}")
    assert not offenders, (
        "use `./scripts/db_query.sh '<SQL>'` — it resolves the path from StackowlHome "
        "and exits non-zero on a missing or empty database:\n" + "\n".join(offenders)
    )


@pytest.mark.tripwire
def test_the_helper_exists_and_refuses_to_re_derive_the_home() -> None:
    """The cure has to BE there, and it has to take the path from one source."""
    script = _ROOT / "scripts" / "db_query.sh"
    assert script.exists(), "scripts/db_query.sh is what the documents now point at"
    body = script.read_text(encoding="utf-8")
    assert "StackowlHome" in body
    # ON THE CONNECT CALL, not merely somewhere in the file. The first version asserted
    # `"mode=ro" in body` and mutation testing killed it: the phrase also appears in the
    # script's own "READ-ONLY BY CONSTRUCTION" comment, so replacing the real connect
    # with a writable one left the guard green. A string present in a file proves nothing
    # about the line that runs — the same lesson as `_ABSENT_SQLITE` two tests down.
    assert 'sqlite3.connect(f"file:{db}?mode=ro", uri=True)' in body, (
        "a query helper must never be what writes the operator's database"
    )
    # The literal must appear only where it is being WARNED about, never as a path used.
    used = [
        ln for ln in body.splitlines()
        if "~/.stackowl" in ln and not ln.lstrip().startswith("#")
    ]
    assert used == [], f"db_query.sh re-derives the home instead of asking: {used}"


@pytest.mark.tripwire
def test_the_detector_fires_on_every_shape_it_exists_for() -> None:
    """A guard that cannot show the bug proves nothing."""
    for line in (
        "sqlite3 stackowl.db \"SELECT 1;\"",
        "sqlite3 ~/.stackowl/workspace/stackowl.db 'SELECT 1;'",
        "cat ~/.stackowl/stackowl.db | wc -c",
    ):
        assert _cannot_query_the_database(f"```bash\n{line}\n```\n"), line


@pytest.mark.tripwire
def test_the_note_correcting_this_defect_is_not_reported_as_the_defect() -> None:
    """The first version of the detector flagged D01.2's own correcting note.

    It sits as bare prose INSIDE a fence — `sqlite3 is NOT installed on this box; query
    through Python instead:` — so scoping to fenced non-comment lines could not exclude
    it. Requiring an ARGUMENT shape after the binary excludes every English sentence by
    construction, which is the difference between a rule and a word list.
    """
    prose = "sqlite3 is NOT installed on this box; query through Python instead:"
    assert _cannot_query_the_database(f"```bash\n{prose}\n```\n") == []
    assert _cannot_query_the_database("```bash\n./scripts/db_query.sh 'SELECT 1;'\n```\n") == []


@pytest.mark.tripwire
def test_a_comment_recording_the_old_command_is_not_a_command() -> None:
    doc = "```bash\n# was: sqlite3 ~/.stackowl/stackowl.db 'SELECT 1;'\n```\n"
    assert _cannot_query_the_database(doc) == []
