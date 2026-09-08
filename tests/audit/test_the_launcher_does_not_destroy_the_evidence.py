"""The one record of a PRE-LOGGING failure must survive the restart that recovers from it.

WHY THIS EXISTS, and it cost a real diagnosis.

MEASURED 2026-09-08: StackOwl ran for 7h21m writing NOT ONE line to `stackowl.jsonl`.
The `@reboot` cron started it at 19:10:07Z, `ps` showed the process alive the whole time,
and the JSONL had nothing after 19:08:27Z. The platform was down all evening and every
instrument said healthy — process alive, `pgrep` finds it, last log line
`orchestrator.run: exit — ready`, tripwire gate green.

A failure that happens BEFORE the log handlers are configured cannot appear in the JSONL
by construction. It appears on the child's own stdout/stderr, which `start.sh` redirects
to `manual_restart_stdout.log`. That file was the only possible witness.

`start.sh` opened it with `>`. So restarting the platform to restore service TRUNCATED the
evidence of why it had stopped — **the recovery destroyed the diagnosis**, and the incident
record has to say "cause unknown" because of it. The file's mtime after the restart was
the restart's own, and its contents were four lines of a healthy boot.

THIS IS THE SHAPE THE REPO ALREADY KNOWS: a write with no reader is defect #1, and this is
its mirror — a reader with no write left to read. It is also why "restart it and see" is
not a diagnosis: the act of looking changed what there was to look at.

WHAT IS ASSERTED is the property, not the line: the launcher must not open the diagnostic
log in a mode that discards what is already there. A future rewrite that keeps the append
is fine; one that goes back to truncation is the defect returning.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_LAUNCHER = _ROOT / "start.sh"

#: The diagnostic file whose whole value is that it OUTLIVES a restart.
_EVIDENCE = "manual_restart_stdout.log"

#: `> file` truncates; `>> file` appends. `2>&1` is a duplication, not a redirect, so it
#: is excluded by requiring a filename after the operator.
_TRUNCATING = re.compile(r"(?<!>)>\s*\"?[^>\s|&]*" + re.escape(_EVIDENCE))


@pytest.mark.tripwire
def test_the_launcher_appends_to_the_evidence_log_and_never_truncates_it() -> None:
    body = _LAUNCHER.read_text(encoding="utf-8")
    lines = [
        (i, ln) for i, ln in enumerate(body.splitlines(), 1)
        if _EVIDENCE in ln and not ln.lstrip().startswith("#")
    ]
    # THE DENOMINATOR. If the launcher stops naming this file at all, the rule has gone
    # blind rather than clean — the same silent-zero this repo keeps paying for.
    assert lines, f"{_LAUNCHER.name} no longer mentions {_EVIDENCE}: rule is blind"
    truncating = [f"{_LAUNCHER.name}:{i}  {ln.strip()}" for i, ln in lines
                  if _TRUNCATING.search(ln)]
    assert not truncating, (
        "the launcher truncates the only record of a failure that happens before logging "
        "is configured, so restarting to restore service destroys the evidence of why it "
        "stopped:\n  " + "\n  ".join(truncating)
    )


@pytest.mark.tripwire
def test_the_rule_would_have_caught_the_line_that_cost_the_diagnosis() -> None:
    """The exact line as it shipped, so the guard is shown to catch its own defect."""
    shipped = 'nohup uv run python -m stackowl start > "$home/manual_restart_stdout.log" 2>&1 &'
    assert _TRUNCATING.search(shipped), "the guard would not have caught the real line"


@pytest.mark.tripwire
def test_the_rule_accepts_the_append_form_and_does_not_cry_wolf() -> None:
    """`>>` is the fix, and `2>&1` beside it must not read as a truncation."""
    fixed = 'nohup uv run python -m stackowl start >> "$home/manual_restart_stdout.log" 2>&1 &'
    assert not _TRUNCATING.search(fixed), "the append form was flagged"
    banner = '} >> "$home/manual_restart_stdout.log"'
    assert not _TRUNCATING.search(banner), "the banner append was flagged"
