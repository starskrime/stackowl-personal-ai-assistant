"""D04.4 — the sixth rediscovery of one bug, prevented instead of patched.

`interaction/classifier_base.py` exists because this defect was found FIVE separate
times. Its own docstring names them: `owls/router.py`, `feedback_classifier.py`,
`owls/evolution.py`, `delivery_gate.py`'s apology generator, and
`schedule_commit_classifier.py` — "five incidents, one root cause, never fixed once
at a shared layer until now."

THE BUG. A reasoning-capable model spends its ENTIRE output budget on invisible
`<think>` tokens before emitting the answer, unless `disable_thinking` is set. The
shared seam sets it by default; **the provider does not** —
`openai_provider.py:1461` reads `bool(kwargs.get("disable_thinking", False))`. So
every call that bypasses the seam runs with reasoning ON.

WHY IT IS INVISIBLE, WHICH IS THE POINT. The reply comes back empty, and the
bypassing sites coerce empty into a benign default:

    owls/shadow_validator.py:342   empty -> quality=None -> "fail closed" -> a good
                                   DNA proposal is silently REJECTED
    pipeline/planner/proposer.py   empty -> frozenset() -> "selected: 0" at INFO,
                                   which reads as a normal quiet turn
    objectives/decomposer.py       small max_tokens AND thinking on — the exact
                                   combination the seam was built for; degrades to a
                                   single-step fallback that looks like a miss
    memory/rollover_summary_handler.py, pipeline/budget/salvage.py
                                   degrade to a floor or to no summary
    tools/meta/owl_build_infer.py  fail-opens to None, and has no timeout either

Nothing errors. Every one of these reads as the system deciding something.

A DOCUMENTED MITIGATION MAKES IT SURVIVABLE, NOT CORRECT. `openai_provider.py`
retries once with `disable_thinking=True` when it sees a reasoning-starved reply, so
the usual cost is a doubled call rather than a wrong answer — except where empty is
coerced to a benign default, which is precisely the list above.

THIS GUARD IS THE FIX FOR THE ROOT CAUSE. Patching six more call sites would be the
sixth rediscovery, not a cure. The allowlist below is the whole design: a call that
genuinely wants reasoning must SAY so, with its reason.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

_SRC = pathlib.Path(__file__).resolve().parents[2] / "src" / "stackowl"

#: Calls that legitimately keep reasoning ON, each with the reason it earns.
#: Reasoning is the PRODUCT for deliberation; a vision/document call resolves its
#: model by capability rather than tier; and a main-work agent loop is not a side task.
_REASONING_IS_WANTED: dict[str, str] = {
    "parliament/positions_synthesis.py":
        "multi-agent deliberation, and its docstring states provider exceptions must "
        "propagate rather than be masked.",
    "tools/io/pdf.py":
        "a DocumentBlock call whose provider is chosen by capability, not tier, and "
        "which needs the exception TEXT for its user-facing error.",
    "vision/analyzer.py":
        "an image call routed by VisionSelector, not by tier.",
    "tools/browser/browse.py":
        "the inner browse agent loop is main work with its own model tier setting.",
    "objectives/driver.py":
        "deliberately runs on the powerful tier via resolve_capable_or_degrade.",
    "pipeline/persistence.py":
        "explicitly excluded by classifier_base's own scope note.",
    "pipeline/steps/classify.py":
        "already correct — goes through LLMGateway with floor==ceiling=='fast'.",
    "skills/synthesizer.py":
        "skill synthesis is generative work, not a verdict.",
    "skills/standard_migration.py":
        "a one-shot migration rewrite, generative rather than a verdict.",
    "memory/reflection_writer_handler.py":
        "reflection prose is the product.",
    "providers/base.py":
        "the provider seam itself.",
    "providers/llm_gateway.py":
        "the gateway forwards **kwargs, so it carries the CALLER's choice through "
        "rather than making one — hardcoding a value here would override every "
        "caller, including the ones that legitimately want reasoning.",
}

# THE DETECTOR ONLY SEES A DIRECTLY-AWAITED CALL, and that limit is stated rather
# than papered over. `moa_runner.py` passes `provider.complete(...)` into a
# `wait_for`/`gather` instead of awaiting it in place, so it is invisible here — and
# it is one of the calls that legitimately wants reasoning anyway. If such a call is
# ever rewritten to a plain `await`, this guard will flag it and the reason gets
# written down at the moment it becomes relevant, which is the right time.


def _provider_complete_calls() -> list[tuple[str, int, bool]]:
    """(relative path, line, passes_disable_thinking) for every LLM completion call.

    `.complete(` alone is not enough: `ParliamentSession.complete()` is a frozen
    dataclass state transition, not an LLM call, and counting it was a false positive
    in the first sweep of this item. The call must be awaited AND take a messages
    argument, which is what an LLM completion looks like.
    """
    found: list[tuple[str, int, bool]] = []
    for path in sorted(_SRC.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Await):
                continue
            call = node.value
            if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute):
                continue
            if call.func.attr != "complete" or not call.args:
                continue
            passes = any(kw.arg == "disable_thinking" for kw in call.keywords)
            found.append((str(path.relative_to(_SRC)), node.lineno, passes))
    return found


@pytest.mark.tripwire
def test_every_side_task_completion_disables_thinking() -> None:
    calls = _provider_complete_calls()
    assert len(calls) >= 10, f"expected the real call surface, found {len(calls)}"

    offenders = [
        f"{path}:{line}" for path, line, passes in calls
        if not passes and path not in _REASONING_IS_WANTED
    ]
    assert not offenders, (
        f"completion call(s) that neither disable thinking nor justify keeping it: "
        f"{offenders}\n"
        "D04.4: the provider defaults `disable_thinking` to FALSE, so a side task that "
        "does not pass it spends its whole output budget on invisible reasoning and "
        "returns empty — which these call sites coerce into a benign default. Pass "
        "`disable_thinking=True`, or add an entry to _REASONING_IS_WANTED saying why "
        "reasoning is the product here."
    )


def test_the_justification_list_still_describes_real_calls() -> None:
    """A list that outlives its subjects stops describing anything."""
    live = {path for path, _line, _passes in _provider_complete_calls()}
    stale = sorted(set(_REASONING_IS_WANTED) - live)
    assert not stale, f"justified but no longer calling complete(): {stale}"


def test_the_detector_can_see_a_bare_completion() -> None:
    """THE CONTROL. Zero offenders is what a clean tree looks like AND what a blind
    detector looks like; without this there is nothing to tell them apart."""
    source = "async def f():\n    result = await provider.complete(messages, model=m)\n"
    tree = ast.parse(source)
    calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Await) and isinstance(n.value, ast.Call)
        and isinstance(n.value.func, ast.Attribute) and n.value.func.attr == "complete"
        and n.value.args
    ]
    assert calls, "the detector cannot see a plain awaited completion call"
    assert not any(kw.arg == "disable_thinking" for kw in calls[0].value.keywords)
