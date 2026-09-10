"""A constant bounded a variable cost, and neither side of the comparison was kept.

MEASURED 2026-09-10 over the 13 retained logs: **26 `evolution.stuck_owl` events**
— `verifier` 12, `hypothesis` 8, `rca_gatherer` 6 — every one `kind: "timeout"`,
`attempts: 2`, `timeout_s: 120`. Steady, 1-3 a day, current.

And the record could not say whether 120s was short by five seconds or by five
hundred, because **nothing anywhere measured what a per-owl evolution costs**:

* `_evolve_one_bounded` logged the BUDGET (`timeout_s`) and never the ELAPSED.
* `coordinator.evolve_one: exit` carried five fields, none of them a duration —
  while `coordinator.execute: exit` has logged the whole BATCH's `duration_ms`
  since it was written. The figure the timeout actually bounds was the one missing.
* `[shadow] validate: exit` carried `n_replayed` — the count — and not the cost.

**AND THE COST WAS ALREADY COMPUTED, THREE LINES AWAY.** `_score_replay` sums
`result_state.step_durations` into `latency_ms` on a `TaskOutcome` built ONLY to
feed the critic prompt, which is never persisted ("this must not touch
task_outcomes"). Every replay measured its own cost and discarded it.

That is the shape this programme has already been taught once, on the history
compressor: *a trigger that is a token count cannot weigh the trade, because the
decision it gates is a COMPARISON and a constant only has one side of it.* And
`health/aggregator.py` states the cure outright — *"A fix that moves a THRESHOLD
must log the MEASUREMENT the threshold is compared against, or its effect is
unfalsifiable."* This file is that rule applied where it had not been.

WHAT THIS DELIBERATELY DOES NOT DO: change the timeout. Choosing a number before
the distribution exists is the guess the missing measurement invites, and one of
these owls has never completed an evolution at all — so the evidence that would
size a budget is exactly what did not exist yet.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2] / "src" / "stackowl"


def _log_field_names(func_src: str, message_fragment: str) -> set[str]:
    """The `_fields` keys of the log call whose message contains *fragment*."""
    # DEDENT FIRST. `inspect.getsource` of a METHOD returns an indented block,
    # and `ast.parse` raises IndentationError on it — which this helper's first
    # draft did, silently turning three assertions into import-time errors that
    # looked like the fix was missing.
    tree = ast.parse(textwrap.dedent(func_src))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        text = "".join(
            a.value for a in node.args
            if isinstance(a, ast.Constant) and isinstance(a.value, str)
        )
        if message_fragment not in text:
            continue
        for kw in node.keywords:
            if kw.arg != "extra" or not isinstance(kw.value, ast.Dict):
                continue
            for k, v in zip(kw.value.keys, kw.value.values, strict=True):
                if isinstance(k, ast.Constant) and k.value == "_fields" and isinstance(v, ast.Dict):
                    return {
                        key.value for key in v.keys
                        if isinstance(key, ast.Constant) and isinstance(key.value, str)
                    }
    return set()


@pytest.mark.tripwire
def test_a_timed_out_owl_reports_the_elapsed_beside_the_budget() -> None:
    """`timeout_s` alone says what we ALLOWED — which a reader can already read off
    the config. `elapsed_s` says what it COST, and only the pair distinguishes an
    owl that died at the wall from one that crashed early and was mislabelled."""
    from stackowl.owls.evolution import EvolutionCoordinator

    src = inspect.getsource(EvolutionCoordinator._evolve_one_bounded)  # noqa: SLF001
    fields = _log_field_names(src, "evolution.stuck_owl")
    assert "timeout_s" in fields, "the stuck marker stopped naming the budget"
    assert "elapsed_s" in fields, (
        "a timeout still reports only the budget it was given — the record cannot "
        "say whether 120s was short by five seconds or by five hundred"
    )


@pytest.mark.tripwire
def test_the_per_owl_evolution_reports_the_figure_the_timeout_bounds() -> None:
    """The BATCH has logged its duration since it was written; the per-owl figure —
    the one `_per_owl_timeout_s` actually bounds — was the missing one."""
    from stackowl.owls.evolution import EvolutionCoordinator

    src = inspect.getsource(EvolutionCoordinator._evolve_one_locked)  # noqa: SLF001
    fields = _log_field_names(src, "coordinator.evolve_one: exit")
    assert "duration_ms" in fields, (
        "the per-owl exit carries no duration, so the successful runs cannot size "
        "a budget for the ones that time out"
    )


@pytest.mark.tripwire
def test_the_shadow_validator_keeps_the_cost_it_already_computes() -> None:
    """BOTH FACTORS. The budget is compared against a PRODUCT — how many replays
    times what each costs — and `n_replayed` alone answers neither half."""
    from stackowl.owls.shadow_validator import ShadowValidator

    src = inspect.getsource(ShadowValidator.validate)
    fields = _log_field_names(src, "[shadow] validate: exit")
    assert "n_replayed" in fields, "the sample size stopped being reported"
    assert "replay_ms" in fields, (
        "the replay cost is still discarded — `_score_replay` computes it three "
        "lines away and puts it on an object nothing persists"
    )
    assert "duration_ms" in fields, "the whole validation still reports no cost"


@pytest.mark.tripwire
def test_every_new_figure_is_reported_at_INFO() -> None:
    """Production runs at INFO and this corpus holds ZERO debug records, so a
    measurement logged at DEBUG is one that never arrives — the failure this
    programme has paid for ten times."""
    from stackowl.owls import evolution, shadow_validator

    for module, fragment in (
        (evolution, "coordinator.evolve_one: exit"),
        (shadow_validator, "[shadow] validate: exit"),
    ):
        tree = ast.parse(Path(inspect.getfile(module)).read_text(encoding="utf-8"))
        levels = set()
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            text = "".join(
                a.value for a in node.args
                if isinstance(a, ast.Constant) and isinstance(a.value, str)
            )
            if fragment in text:
                levels.add(node.func.attr)
        assert levels and levels <= {"info", "warning", "error"}, (
            f"{fragment} is logged at {levels or 'nothing'} — a cost recorded below "
            "INFO is a cost nobody will ever read"
        )


@pytest.mark.tripwire
def test_the_batch_states_its_worst_case_against_the_budget() -> None:
    """A FIELD makes the question answerable; a SENTENCE makes it asked.

    The per-owl durations land on `evolve_one: exit` as fields, so "is 120s
    short?" would mean joining records — and a question that costs a join is one
    nobody asks. The batch summary states the worst case beside the budget, which
    is the entire comparison the constant was missing.
    """
    from stackowl.owls import evolution

    src = Path(inspect.getfile(evolution)).read_text(encoding="utf-8")
    assert "slowest owl this batch took" in src, "the batch no longer states its worst case"

    tree = ast.parse(src)
    guarded = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        body = ast.dump(node)
        if "slowest owl this batch took" in body and "slowest_owl" in ast.dump(node.test):
            guarded = True
    assert guarded, (
        "the summary is emitted unguarded — a batch in which every owl timed out "
        "has no completed duration, and printing a confident 0.0s there would be "
        "the instrument lying about the very case it exists to describe"
    )


@pytest.mark.tripwire
def test_the_timeout_itself_is_unchanged_and_that_is_deliberate() -> None:
    """THE CONTROL, and it is the half that keeps this honest.

    The tempting fix is to raise the number. One of the three stuck owls has never
    completed an evolution, so the distribution that would size a budget does not
    exist yet — picking one now is exactly the guess the missing measurement
    invites. If a later change moves it, this fails and whoever moves it has to
    say what evidence they used.
    """
    from stackowl.owls import evolution

    tree = ast.parse(Path(inspect.getfile(evolution)).read_text(encoding="utf-8"))
    defaults = [
        node.value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(getattr(t, "id", "") == "EVOLUTION_PER_OWL_TIMEOUT_SECONDS" for t in node.targets)
        and isinstance(node.value, ast.Constant)
    ]
    assert defaults == [120.0], (
        f"the per-owl timeout moved to {defaults} — that is a threshold change, and "
        "it needs the duration distribution this item exists to produce, not a guess"
    )
