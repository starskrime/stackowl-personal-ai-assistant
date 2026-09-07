"""A Verification command must survive the `grep` it is actually run with.

MEASURED 2026-09-07, on a measurement this loop had just made and was one edit away from
writing into a design document as fact.

``grep -h <pattern> ~/.stackowl/logs/stackowl*.jsonl | tail -1`` is the idiom for "the
newest record", and it is correct only if grep emits its files in ARGUMENT order.  The
harness this programme runs inside replaces ``grep`` with a shell function wrapping a
MULTI-THREADED grep, which emits results as workers finish.  Three identical runs of that
exact command returned last-line timestamps 2026-09-03, 2026-09-04 and 2026-09-03 while
the true newest record was 2026-09-07; the current log file landed SEVENTH of eleven in
the output stream.

Two things make this worth a guard rather than a note:

* It was CREATED by the cure for another report.  ``BLIND AFTER MIDNIGHT`` exists to move
  these queries off the single ``stackowl.jsonl`` onto the ``stackowl*.jsonl`` glob.  The
  single file is at least ordered.  Multi-file is precisely the case that is not.
* A COUNT is unaffected, so every count in the corpus is right and nothing looked wrong.
  Only the queries that pick ONE record are damaged, and those are the ones documents use
  to say "and here is the latest".

The cure is one word — ``| sort |`` before the ``tail`` — and it works because every line
these logs contain begins ``{"ts": "``.  That is a property of the formatter, not a
convention, so it is asserted below: the day ``ts`` stops being the first key, a lexical
sort silently stops being a chronological one and every corrected command rots at once.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from stackowl.infra.observability import JsonlFormatter

from scripts.doc_check import _newest_by_pipe_position, _shell_skeleton

_DESIGNS = Path(__file__).resolve().parents[2] / "docs" / "reference-mapping" / "designs"


@pytest.mark.tripwire
def test_the_formatter_still_writes_ts_first() -> None:
    """`| sort` is only a chronological sort while `ts` is the first key on the line."""
    record = logging.LogRecord(
        name="stackowl.tool", level=logging.INFO, pathname=__file__, lineno=1,
        msg="[tools] registry.with_defaults: discovered", args=(), exc_info=None,
    )
    line = JsonlFormatter().format(record)
    assert line.startswith('{"ts": "'), line[:60]
    assert next(iter(json.loads(line))) == "ts"


@pytest.mark.tripwire
def test_no_design_document_takes_the_newest_record_by_pipe_position() -> None:
    """The live corpus guard — and it prints its denominator on failure."""
    offenders: list[str] = []
    watched = 0
    for doc in sorted(_DESIGNS.glob("*.md")):
        for ln, cmd, is_sorted in _newest_by_pipe_position(doc.read_text(encoding="utf-8")):
            watched += 1
            if not is_sorted:
                offenders.append(f"{doc.name}:{ln}  {cmd[:100]}")
    assert not offenders, (
        f"{len(offenders)} of {watched} position-taking log queries do not sort first; "
        "add `| sort |` before the tail/head:\n" + "\n".join(offenders)
    )


@pytest.mark.tripwire
def test_the_detector_fires_on_the_shape_it_exists_for() -> None:
    """A guard that cannot show the bug proves nothing — so show it."""
    doc = (
        "```bash\n"
        "grep -h 'x' ~/.stackowl/logs/stackowl*.jsonl | tail -1\n"
        "```\n"
    )
    hits = _newest_by_pipe_position(doc)
    assert [(ln, is_sorted) for ln, _, is_sorted in hits] == [(2, False)]

    fixed = doc.replace("| tail -1", "| sort | tail -1")
    assert [is_sorted for _, _, is_sorted in _newest_by_pipe_position(fixed)] == [True]


@pytest.mark.tripwire
def test_an_alternation_in_the_pattern_is_not_read_as_a_shell_pipe() -> None:
    """The regression that the first version of this detector actually had.

    Its pipeline-segment rule was ``[^|]*`` between the reader and the glob, which a
    pattern like ``'(discover_tools|register_server_tools)'`` breaks — D16.5 was missed
    entirely until the quote-blanking skeleton replaced it.
    """
    doc = (
        "```bash\n"
        "grep -E '\"msg\": \"mcp\\.client\\.(discover_tools|register_server_tools)' \\\n"
        "  ~/.stackowl/logs/stackowl*.jsonl | tail\n"
        "```\n"
    )
    assert [(ln, is_sorted) for ln, _, is_sorted in _newest_by_pipe_position(doc)] == [
        (2, False)
    ]
    assert "|" not in _shell_skeleton("grep -E '(a|b)' f")


@pytest.mark.tripwire
def test_an_ordered_reader_is_not_reported() -> None:
    """`cat` and `jq` consume their arguments in order; reporting them cries wolf."""
    for line in (
        "cat ~/.stackowl/logs/stackowl*.jsonl | jq -c '.fields' | tail -1",
        "jq -rc 'select(.msg|test(\"boot\"))|.ts' ~/.stackowl/logs/stackowl*.jsonl | tail -1",
    ):
        assert _newest_by_pipe_position(f"```bash\n{line}\n```\n") == [], line


@pytest.mark.tripwire
def test_a_comment_recording_a_past_run_is_not_a_command() -> None:
    """Prose and `#` records are where denials live; only runnable lines count."""
    doc = (
        "```bash\n"
        "# RAN 2026-09-01: grep -h 'x' ~/.stackowl/logs/stackowl*.jsonl | tail -1\n"
        "```\n"
    )
    assert _newest_by_pipe_position(doc) == []
