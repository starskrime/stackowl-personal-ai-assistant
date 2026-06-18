"""REACT-2 / F028 — the provider tool-loop iteration ceiling agrees with the
default per-turn step backstop.

``ProviderConfig.tool_max_iterations`` (the provider's own loop ceiling) defaulted
to 30 while ``DEFAULT_TURN_MAX_STEPS`` (the governor's default backstop) is 20.
The two bounds were independently configured and disagreed: on the no-explicit-caps
path the governor cuts at 20, yet the provider ceiling sat 10 higher, so the loop
ceiling could silently become the bound (and an uncounted wrap-up generation could
run as a 21st+ step). They must be derived from one source so they never drift.
"""
from __future__ import annotations

from stackowl.authz.bounds import DEFAULT_TURN_MAX_STEPS
from stackowl.config.provider import ProviderConfig


def test_tool_max_iterations_default_aligns_with_turn_step_backstop():
    cfg = ProviderConfig(
        name="p", protocol="openai", default_model="m", tier="standard",
    )
    assert cfg.tool_max_iterations == DEFAULT_TURN_MAX_STEPS, (
        "the provider iteration ceiling default must equal the default turn step "
        "backstop so the two bounds never disagree"
    )
