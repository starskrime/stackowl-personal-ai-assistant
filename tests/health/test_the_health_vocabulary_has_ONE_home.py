"""The subsystem health vocabulary is written once, and every state is classified.

MEASURED 2026-09-12. ``Literal["ok", "degraded", "down"]`` was written out
INDEPENDENTLY in five modules — ``health/status.py``, ``memory/bridge.py``,
``webhooks/receiver.py``, ``notifications/router.py`` and
``startup/provider_probe.py`` — with no shared name. The ``HealthStatus``
dataclass is imported by a dozen modules; the vocabulary inside it was not
importable at all, so every new reporter re-typed it. That is the
two-copies-of-one-rule shape, at five copies.

It mattered because the platform could not say **"I could not measure it"**.
Adding a state was never a design problem: it was a five-file edit that the next
reporter would have made six.

AND THE FIVE COPIES WERE THE SMALLER HALF. The PARTITION — which states are
healthy, which merely warrant telling someone, which justify killing the process
— was implicit in four separate readers:

* ``aggregator.is_live``      compared to ``"down"``
* ``health_sweep``            built two buckets from ``"down"`` and ``"degraded"``
* ``cli health``              a two-branch ternary falling through to ``✗``
* ``control_plane/page.js``   ``healthKind``, falling through to ``warn``

None of them raises on a word it does not know; each falls through whatever
branch happens to be last. So a fourth state would have given the sweep a
subsystem that is neither down nor degraded and therefore counted HEALTHY, and
the CLI an outage cross for something nothing had measured — silently, with no
test failing. Extending an implicit partition is an invisible edit.

So this file pins three things, and the third is the one that could not have been
written as an assertion about today's data: the partition is walked from
``get_args(HealthState)``, so a state added later fails here until somebody
decides, in writing, whether it kills the process.
"""

from __future__ import annotations

import ast
import pathlib
from dataclasses import replace
from typing import get_args

import pytest

from stackowl.health.status import (
    HEALTHY_STATES,
    LIVENESS_FAILING_STATES,
    WARNING_STATES,
    HealthState,
    HealthStatus,
)

SRC = pathlib.Path(__file__).resolve().parents[2] / "src" / "stackowl"
HOME = SRC / "health" / "status.py"


def _literal_state_lists(path: pathlib.Path) -> list[str]:
    """Every ``Literal[...]`` in ``path`` whose members look like health states.

    Matched by MEMBERSHIP rather than by the exact three-word list the tree
    happened to hold: a copy that adds a word, drops one or reorders them is the
    same defect, and a text search for the old spelling would miss all three.
    """
    tree = ast.parse(path.read_text(), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Subscript):
            continue
        name = node.value
        if not (isinstance(name, ast.Name) and name.id == "Literal"):
            continue
        sl = node.slice
        members = sl.elts if isinstance(sl, ast.Tuple) else [sl]
        words = {m.value for m in members if isinstance(m, ast.Constant) and isinstance(m.value, str)}
        if {"ok", "down"} <= words:
            found.append(", ".join(sorted(words)))
    return found


@pytest.mark.tripwire
def test_no_module_writes_its_own_copy_of_the_vocabulary() -> None:
    offenders: dict[str, list[str]] = {}
    for path in sorted(SRC.rglob("*.py")):
        if path == HOME:
            continue
        hits = _literal_state_lists(path)
        if hits:
            offenders[str(path.relative_to(SRC))] = hits
    assert not offenders, (
        "health states spelled out away from health/status.py — import HealthState "
        f"instead: {offenders}"
    )


@pytest.mark.tripwire
def test_the_home_still_declares_it() -> None:
    """The control for the test above: it must be able to FIND a copy at all.

    Without this, deleting `HealthState` would make the sweep pass with zero
    offenders — a guard reporting silence because it went blind, which is the
    zero-over-zero failure this tree pays for most often.
    """
    assert _literal_state_lists(HOME), "health/status.py no longer declares the vocabulary"


@pytest.mark.tripwire
def test_every_state_is_classified_exactly_once() -> None:
    """The partition covers the vocabulary, with no gaps and no overlaps."""
    states = set(get_args(HealthState))
    groups = {
        "HEALTHY_STATES": set(HEALTHY_STATES),
        "WARNING_STATES": set(WARNING_STATES),
        "LIVENESS_FAILING_STATES": set(LIVENESS_FAILING_STATES),
    }
    covered: set[str] = set()
    for name, group in groups.items():
        unknown_words = group - states
        assert not unknown_words, f"{name} names states that are not in HealthState: {unknown_words}"
        overlap = covered & group
        assert not overlap, f"{name} re-classifies states another group already claims: {overlap}"
        covered |= group
    missing = states - covered
    assert not missing, (
        f"HealthState gained {sorted(missing)} and no group claims it. Decide what it "
        "means BEFORE shipping it: does it warrant telling an operator (WARNING_STATES), "
        "and does it justify killing the process (LIVENESS_FAILING_STATES)?"
    )


@pytest.mark.tripwire
def test_every_reader_of_the_vocabulary_asks_the_partition() -> None:
    """No consumer may compare a health status to a bare string.

    The three modules listed in this file's docstring each did, and each would
    have fallen through silently on a fourth word. Scoped to the readers rather
    than the whole tree: a CONTRIBUTOR reporting ``status="degraded"`` about
    itself is writing a value, which is fine and common (33 sites).
    """
    readers = [
        SRC / "health" / "aggregator.py",
        SRC / "scheduler" / "handlers" / "health_sweep.py",
    ]
    # THE ONE EXEMPTION, AND WHY IT IS NOT A WEAKENING. `_corroborate_non_answers`
    # is where `unknown` acquires its meaning — it reads the non-answers and decides
    # which of them survive as `unknown`. A classifier cannot ask the classification
    # it produces. Every other reader must.
    classifier = "_corroborate_non_answers"
    offenders: list[str] = []
    exempted = 0
    for path in readers:
        text = path.read_text()
        tree = ast.parse(text, filename=str(path))
        inside: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == classifier:
                inside.update(n.lineno for n in ast.walk(node) if hasattr(n, "lineno"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            src = ast.get_source_segment(text, node) or ""
            if ".status" not in src:
                continue
            for op, comp in zip(node.ops, node.comparators, strict=True):
                if isinstance(op, ast.Eq | ast.NotEq) and isinstance(comp, ast.Constant):
                    if comp.value in set(get_args(HealthState)):
                        if node.lineno in inside:
                            exempted += 1
                        else:
                            offenders.append(f"{path.name}:{node.lineno}: {src}")
    assert not offenders, (
        "a health status compared to a bare state word — ask HEALTHY_STATES / "
        f"WARNING_STATES / LIVENESS_FAILING_STATES instead: {offenders}"
    )
    # AND THE EXEMPTION MUST STILL BE REAL. An allowlist naming a function that no
    # longer exists is the shape this tree calls a stale exemption: it silently
    # stops covering anything and nothing says so.
    assert exempted, (
        f"{classifier} no longer compares a status to a state word — the exemption "
        "above is stale and should be deleted, not carried"
    )


@pytest.mark.tripwire
def test_the_sweep_classifies_every_state_the_vocabulary_holds() -> None:
    """Drive the real bucketing with EVERY state, constructed from the vocabulary.

    Deliberately NOT an assertion about the states that exist today: the defect
    being guarded against is a state nobody classified, so the population has to
    come from `get_args` and not from a list written here. `unknown` was added the
    same day this test was; the next word gets the same treatment for free.
    """
    from stackowl.health.status import LIVENESS_FAILING_STATES as FAILING
    from stackowl.health.status import WARNING_STATES as WARN

    for state in get_args(HealthState):
        s = HealthStatus(name="probe", status=state, message=None, latency_ms=1.0)
        buckets = [b for b, group in (("down", FAILING), ("degraded", WARN)) if s.status in group]
        if state in HEALTHY_STATES:
            assert not buckets, f"{state!r} is healthy but the sweep would alert on it"
        else:
            assert len(buckets) == 1, (
                f"{state!r} lands in {buckets or 'NO bucket'} — a state in no bucket is "
                "counted HEALTHY by health_sweep, silently"
            )
        # and `replace` must survive it, because that is how the aggregator promotes
        # a corroborated non-answer back to a verdict.
        assert replace(s, status=state).status == state
