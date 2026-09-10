"""Ranking an alarm channel by TOTAL promotes startup facts in proportion to how
much code was edited that week.

MEASURED 2026-09-10, and it cost this loop an item. `retired_log_messages.py` was
used three times today to pick work by ranking WARNING-and-above messages by raw
occurrence count. The top entry was `[tools] registry.register: thin tool
description` at **827** — the largest single message in the corpus, all 827 naming
ONE learned tool whose description has not changed since 2026-08-28.

An item was built on it: the lint "must" be re-announcing a static fact. **IT WAS
NOT.** Bracketing the corpus by process lifetimes: **827 windows with exactly one
occurrence, 269 with none, and ZERO with more than one.** The lint already did
precisely what its docstring says — surfaced once, at registration, per process.
The fix was reverted before it shipped, because a dedupe with nothing to dedupe is
decoration, and decoration is the shape this repo names most.

WHAT THE TOTAL ACTUALLY MEASURED. 1,096 process lifetimes are in this corpus, and
**580 of those boots are CodeWatcher re-execs** — this programme editing the
instance it is measuring, which `CLAUDE.md` already warns about in the mirror
("a measurement taken on this box while this loop runs may be measuring the
loop"). So 827 is a number about DEVELOPMENT CHURN wearing the shape of a defect.

THE RATIO IS NOT THE DISCRIMINATOR, and the first cut got that wrong too: a
0.8–1.2 per-boot band is a magic number, and it MISSES this message, which sits at
0.75/boot because it fires in 827 of 1,096 lifetimes and not in the other 269. The
property that settles it is the MAXIMUM: a message that never fired twice in one
process cannot be a recurring condition, whatever its total.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location(
        "retired_log_messages", _ROOT / "scripts" / "retired_log_messages.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["retired_log_messages"] = mod
    spec.loader.exec_module(mod)
    return mod


def _log(tmp_path: pathlib.Path, records: list[tuple[str, str, str]]) -> pathlib.Path:
    """Write a jsonl log from (ts, level, msg) triples."""
    p = tmp_path / "stackowl-test.jsonl"
    p.write_text(
        "\n".join(
            json.dumps({"ts": ts, "level": level, "msg": msg})
            for ts, level, msg in records
        ),
        encoding="utf-8",
    )
    return p


def _boot(ts: str) -> tuple[str, str, str]:
    return (ts, "INFO", "[startup] browser_probe.check: exit libs=True ready=True")


@pytest.mark.tripwire
def test_a_once_per_process_message_is_named_a_startup_fact(tmp_path) -> None:
    """THE 827, in miniature: three lifetimes, one occurrence each."""
    mod = _load()
    log = _log(tmp_path, [
        _boot("2026-09-01T00:00:00"), ("2026-09-01T00:00:01", "WARNING", "thin"),
        _boot("2026-09-01T01:00:00"), ("2026-09-01T01:00:01", "WARNING", "thin"),
        _boot("2026-09-01T02:00:00"), ("2026-09-01T02:00:01", "WARNING", "thin"),
    ])
    boots, maxima = mod.per_boot([log], {"WARNING"})
    assert boots == 3
    assert maxima["thin"] == 1, "three occurrences across three lifetimes is max 1"


@pytest.mark.tripwire
def test_a_RECURRING_condition_is_not_flagged(tmp_path) -> None:
    """THE CONTROL, and the whole reason this is a distinction rather than a filter.

    A message that fires repeatedly WITHIN one process is a real condition and
    must keep its rank. Without this, "flag the loud ones" would hide exactly the
    defects the report exists to surface.
    """
    mod = _load()
    log = _log(tmp_path, [
        _boot("2026-09-01T00:00:00"),
        ("2026-09-01T00:00:01", "WARNING", "locked"),
        ("2026-09-01T00:00:02", "WARNING", "locked"),
        ("2026-09-01T00:00:03", "WARNING", "locked"),
        _boot("2026-09-01T01:00:00"),
        ("2026-09-01T01:00:01", "WARNING", "locked"),
    ])
    boots, maxima = mod.per_boot([log], {"WARNING"})
    assert boots == 2
    assert maxima["locked"] == 3, "it fired three times in ONE process"


@pytest.mark.tripwire
def test_the_RATE_alone_would_have_missed_it(tmp_path) -> None:
    """Why the magic band was replaced, pinned as a test rather than a claim.

    Four lifetimes, the message in three of them: 0.75/boot — outside any
    "~1 per boot" band — while the maximum is still 1. This is the live shape
    exactly (827 of 1,096), and the band is what let the wrong item through.
    """
    mod = _load()
    log = _log(tmp_path, [
        _boot("2026-09-01T00:00:00"), ("2026-09-01T00:00:01", "WARNING", "thin"),
        _boot("2026-09-01T01:00:00"), ("2026-09-01T01:00:01", "WARNING", "thin"),
        _boot("2026-09-01T02:00:00"), ("2026-09-01T02:00:01", "WARNING", "thin"),
        _boot("2026-09-01T03:00:00"),
    ])
    boots, maxima = mod.per_boot([log], {"WARNING"})
    assert boots == 4
    assert 3 / boots == 0.75, "the live rate, reproduced"
    assert maxima["thin"] == 1, "the rate is unremarkable; the maximum is decisive"


@pytest.mark.tripwire
def test_the_window_at_each_END_of_the_corpus_still_counts(tmp_path) -> None:
    """A retained log starts AND ends mid-process, and both edges are real.

    THE TRAILING EDGE IS THE ONE THAT NEEDED A TEST. The leading window is
    flushed by the marker that follows it, so an assertion about it passes even
    with the final flush deleted — mutation testing proved exactly that, and the
    first version of this test was vacuous for the line it named. The CURRENT
    process is the trailing window, so losing it would blind the report to
    whatever is happening right now.
    """
    mod = _load()
    log = _log(tmp_path, [
        ("2026-09-01T00:00:01", "WARNING", "early"),
        ("2026-09-01T00:00:02", "WARNING", "early"),
        _boot("2026-09-01T01:00:00"),
        ("2026-09-01T01:00:01", "WARNING", "late"),
        ("2026-09-01T01:00:02", "WARNING", "late"),
        ("2026-09-01T01:00:03", "WARNING", "late"),
    ])
    _boots, maxima = mod.per_boot([log], {"WARNING"})
    assert maxima["early"] == 2, "the leading window was dropped"
    assert maxima["late"] == 3, (
        "the TRAILING window was dropped — that is the process running right now"
    )


@pytest.mark.tripwire
def test_the_report_prints_its_denominator(tmp_path) -> None:
    """A result that carries its own denominator cannot mislead the same way twice.

    This is the repo's own rule about what a denominator is MADE OF, applied to
    the instrument that reads the alarm channel.
    """
    mod = _load()
    import inspect

    src = inspect.getsource(mod.main)
    assert "process lifetimes" in src
    assert "STARTUP FACT" in src
    assert "CodeWatcher" in src, (
        "the reader must be told WHY a boot count is not a neutral denominator on "
        "this box"
    )
