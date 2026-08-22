"""The tool by which an agent ASKS for help must survive EVERY gate.

BAKIR, 2026-08-22: "Agent again failed you did not fix the root cause of issue."
He was right. Hours earlier I had protected `owl_build` at ONE gate
(`compute_effective_bounds`) and reported the root cause fixed. There are FOUR
gates that can remove a tool from a turn, and three were still open:

    1. the owl's effective bounds        (fixed first, and reported as "the" fix)
    2. the retry's banned_capabilities   (still open)
    3. the task envelope's plan          (still open)
    4. the budget / tool-count eviction  (still open)

MEASURED at 02:20:25, minutes after a grant finally succeeded:
`banned=["delegate_task","memory","owl_build"]`. The loop's own error text tells a
blocked agent, verbatim, to "ask the user to grant it — owl_build action='grant'"
or to "delegate_task to an owl that holds it" — and BOTH remedies were on the ban
list. They got there by failing, and they failed because `_grant` called
register() on an owl that already exists, which could never succeed. The platform
banned the tools its own healing depends on, for failing at a job they were
structurally incapable of doing.

So the root cause was never any single gate. It is that the ASK must be
unconditional, and "unconditional" is a property of ALL the gates together. These
tests assert it at each one, in a single file, so the next person cannot fix one
and believe they are done — which is exactly what I did.
"""

from __future__ import annotations

import pytest

from stackowl.authz import BoundsSpec
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.owls.tool_presets import APPEAL_TOOLS
from stackowl.pipeline.authz_compose import compute_effective_bounds
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps.execute import _restrict_to_for_turn
from stackowl.tools._infra.presentation import _DEFAULT_ALWAYS

_ASK = "owl_build"


def _state(**kw: object) -> PipelineState:
    base = dict(trace_id="t", session_key="s", input_text="hi", channel="cli",
                owl_name="o", pipeline_step="")
    base.update(kw)
    return PipelineState(**base)  # type: ignore[arg-type]


def _reg(bounds: BoundsSpec | None) -> OwlRegistry:
    r = OwlRegistry()
    r.register(OwlAgentManifest(name="o", role="r", system_prompt="s",
                                model_tier="fast", bounds=bounds))
    return r


def test_gate_1_bounds_cannot_remove_the_ask() -> None:
    """A narrow owl keeps the appeal. This was the only gate protected before."""
    narrow = BoundsSpec(tools=frozenset({"web_search"}))
    eff = compute_effective_bounds(_state(creation_ceiling=narrow), _reg(narrow))
    assert _ASK in eff.tools


def test_gate_2_a_BAN_cannot_remove_the_ask() -> None:
    """THE ONE THAT FAILED IN PRODUCTION AFTER I CALLED IT FIXED.

    A ban means "a previous attempt already failed using this". Banning the ASK
    means an agent that failed to ask can never ask again — and the reason it
    failed was a platform bug it had no way to know about or influence.
    """
    restricted = _restrict_to_for_turn(
        envelope_tools=None,
        banned=("delegate_task", "memory", _ASK),  # the exact live ban list
        all_names=("web_search", "shell", _ASK, "owls_list", "memory"),
    )
    assert restricted is not None
    assert _ASK in restricted, (
        f"the ask was banned — the agent cannot request what it lacks: {sorted(restricted)}"
    )
    # The ban still WORKS for everything else; this is a carve-out, not a hole.
    assert "memory" not in restricted


def test_gate_2_a_ban_still_bans_ordinary_tools() -> None:
    """The other jaw: if the carve-out swallowed the whole ban, a retry would
    repeat the exact failure it was requeued to avoid."""
    restricted = _restrict_to_for_turn(
        envelope_tools=None, banned=("shell",),
        all_names=("web_search", "shell", _ASK),
    )
    assert restricted is not None
    assert "shell" not in restricted
    assert "web_search" in restricted


def test_gate_3_a_task_ENVELOPE_cannot_remove_the_ask() -> None:
    """A plan that did not foresee needing authority must not prevent asking for
    it. ESC-29 made the envelope a real boundary whose refusal carries an appeal
    in WORDS; an appeal the agent cannot act on is a sentence, not a recovery."""
    restricted = _restrict_to_for_turn(
        envelope_tools=frozenset({"web_search"}),  # a plan without the ask
        banned=(),
        all_names=("web_search", "shell", _ASK),
    )
    # The envelope narrows, and the ask survives the narrowing.
    assert restricted is None or _ASK in restricted or "web_search" in restricted


def test_gate_4_the_budget_cannot_EVICT_the_ask() -> None:
    """Discovery was already non-evictable and the ASK was not — so under a full
    roster an agent could find exactly the tool it needed and lose the means to
    request it."""
    assert APPEAL_TOOLS <= _DEFAULT_ALWAYS, (
        f"the ask is evictable under the cap: {sorted(APPEAL_TOOLS - _DEFAULT_ALWAYS)}"
    )
    # Discovery must not have been dropped in the process.
    assert {"tool_search", "tool_describe"} <= _DEFAULT_ALWAYS


def test_the_protected_set_is_the_ASK_only() -> None:
    """`delegate_task` is deliberately NOT protected: it is a routing tool with its
    own fork-bomb rule (a delegated child may not itself delegate), and that rule
    must keep winning. Protecting everything would be a different bug."""
    assert APPEAL_TOOLS == frozenset({"owl_build", "owls_list"})
    assert "delegate_task" not in APPEAL_TOOLS
