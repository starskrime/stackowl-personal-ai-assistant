"""The tool cap was written down twice and only the copy that never runs was fixed.

`pipeline/context_budget.py` records an OWNER DECISION, 2026-07-22, in the comment
above `HARD_TOOL_COUNT_CAP`:

    Raised 2026-07-22 (owner decision): the registered toolset (~60-78 tools) was
    already exceeding the old cap of 40, silently trimming real tools every turn
    — this is a backstop against a pathological catalog, not a shaping lever, so
    it should sit comfortably above any real toolset, not below it.

That constant became 150. `OrchestratorSettings.tool_count_cap` — the value that
is ACTUALLY USED, because settings are always present — kept `default=40`.
`HARD_TOOL_COUNT_CAP` is only the fallback for when settings are absent, so the
decision applied precisely when it could not matter.

TWO COPIES OF ONE RULE, AND THE ONE THAT RUNS WAS NOT THE ONE THAT WAS FIXED.

MEASURED 2026-09-08 on the live platform: **1,491 turns presented 79 tools** — the
whole catalogue — because this box's `stackowl.yaml` overrides the cap to 150 by
hand. Every install that clones from GitHub gets 40, and clips ~39 of 79 tools on
every single turn. The document that reasoned about this (D05.8) measured "150
against a 79-tool catalogue, structurally unreachable" — on the OVERRIDE, not on
what users ship with. That is "fix the platform, never this setup", one level up:
the setup was fixed and the platform was not.

AND THE DEFAULT ENCODED A RELATIONSHIP, NOT A NUMBER. Its own test says why —
"the shipped default keeps behavior byte-identical (FR5: capable model = full
set)" — which was TRUE when the catalogue was ~20 tools and 40 comfortably
exceeded it. The catalogue grew to 79; the constant did not move; the intent
silently stopped holding. A constant that expresses `cap >= catalogue` becomes a
binding limit the moment the other side grows, and nothing was watching.

ESC-35 (2026-08-23) reached the same place from the other direction: "Demote
tool_count_cap to an opt-out ceiling and size the presented set against the
resolved model window." The window-relative sizing ALREADY EXISTS — the budgeted
path passes `budget={"window": …, "fixed_cost_tokens": …, "max_tools": …}` — and
could never take effect underneath a count that bound first at 40.

So the default now IS the backstop, by reference rather than by repetition.
"""

from __future__ import annotations

import ast
import inspect
import textwrap

import pytest

from stackowl.config.settings import OrchestratorSettings
from stackowl.pipeline.context_budget import HARD_TOOL_COUNT_CAP


@pytest.mark.tripwire
def test_the_shipped_default_is_the_backstop_not_a_second_number() -> None:
    """The value users get must be the one the owner decision sized."""
    assert OrchestratorSettings().tool_count_cap == HARD_TOOL_COUNT_CAP, (
        f"the shipped default is {OrchestratorSettings().tool_count_cap} while the "
        f"backstop the owner decision sized is {HARD_TOOL_COUNT_CAP}. A default BELOW "
        f"the registered toolset trims real tools on every turn of every install that "
        f"has not hand-edited its yaml"
    )


@pytest.mark.tripwire
def test_the_default_is_a_REFERENCE_so_the_two_cannot_diverge_again() -> None:
    """Equality today is not the property — INABILITY TO DIVERGE is.

    The two agreed once before and drifted apart when only one was raised. A test
    that merely compares them would go green again the moment someone re-typed a
    literal that happened to match, and then rot exactly as the first pair did.
    So this reads the SOURCE: the field's default must be a name, not a number.
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(OrchestratorSettings)))
    field = None
    for node in ast.walk(tree):
        if (isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
                and node.target.id == "tool_count_cap"):
            field = node.value
    assert field is not None, "tool_count_cap is no longer an annotated field"
    default = next(
        (kw.value for kw in getattr(field, "keywords", []) if kw.arg == "default"),
        None,
    )
    assert default is not None, "tool_count_cap has no explicit default"
    assert not isinstance(default, ast.Constant), (
        "the default is a literal again. It must REFERENCE the backstop constant, "
        "so raising one raises both — writing the number twice is exactly how the "
        "cap came to say 150 in the place that never runs and 40 in the place that "
        "does"
    )


@pytest.mark.tripwire
def test_the_backstop_still_sits_above_a_real_catalogue() -> None:
    """The relationship the decision states, not the number it happened to pick.

    "It should sit comfortably above any real toolset, not below it." The live
    platform presents 79 tools; a backstop at or under that is the 2026-07-22
    defect returning. This is deliberately a RELATIONSHIP so it survives the
    catalogue growing again — which is the way it broke the first time.
    """
    measured_catalogue_2026_09_08 = 79
    assert measured_catalogue_2026_09_08 < HARD_TOOL_COUNT_CAP, (
        f"the backstop ({HARD_TOOL_COUNT_CAP}) no longer sits above the catalogue "
        f"measured live ({measured_catalogue_2026_09_08} tools presented on 1,491 "
        f"turns). A backstop below the real toolset is a shaping lever nobody chose"
    )
