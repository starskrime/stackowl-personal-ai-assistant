"""A tier ladder whose rungs are the same model is a no-op, and it said nothing.

D04.4 is filed as "No single seam for 'cheap model work', so cost/latency of side
tasks is unmanaged." The seam EXISTS — `get_by_tier`, `get_with_cascade`,
`resolve_tier_with_fallback`, and call sites all over the codebase asking for
``floor="fast"``. What does not exist is anything cheaper to route TO.

MEASURED 2026-09-03 on the live configuration:

    qwen36           enabled: false   http://172.30.60.31  qwen3.6:35b   [fast]
    ollama-standard  enabled: false   http://172.30.60.31  qwen3.6:35b   [standard]
    ollama-powerful  enabled: false   http://172.30.60.31  qwen3.6:35b   [powerful]
    NeraAiRaw        enabled: TRUE    llm-gateway…         neraai-v1-raw [fast, standard, powerful]

Three of the four are disabled, all on the same unreachable host, and all three
name THE SAME MODEL — that is one model wearing three hats, not a ladder. The one
enabled provider claims every tier, so every rung resolves to it. Seven days of
cost_records agree: 15,358 calls, 174M input tokens, ONE model.

So the classify summariser, the critic, owl_build inference and inner browse all
ask for something cheap and are handed the flagship, and no log line, health
surface or test ever said so. This makes the condition VISIBLE. Whether to wire a
genuinely cheaper model is a hardware and cost decision — ESC-111, his call.
"""

from __future__ import annotations

import inspect

from stackowl.providers.registry import ProviderRegistry


def _registry_with(routes: dict[str, list[tuple[str, list[str]]]]) -> ProviderRegistry:
    """A registry whose tier map is set directly — the config loader is not what
    is under test here, the resolution is."""
    from types import SimpleNamespace

    reg = ProviderRegistry.__new__(ProviderRegistry)
    reg._providers = {n: SimpleNamespace(name=n) for n in routes}  # noqa: SLF001
    reg._tiers = {  # noqa: SLF001
        n: tuple(SimpleNamespace(model=m, tiers=tuple(t)) for m, t in rs)
        for n, rs in routes.items()
    }
    return reg


def test_one_provider_claiming_every_tier_is_reported_DEGENERATE() -> None:
    """The live shape on this box."""
    reg = _registry_with({"NeraAiRaw": [("neraai-v1-raw", ["fast", "standard", "powerful"])]})

    assert reg.describe_tier_ladder() == {
        "fast": "NeraAiRaw/neraai-v1-raw",
        "standard": "NeraAiRaw/neraai-v1-raw",
        "powerful": "NeraAiRaw/neraai-v1-raw",
    }
    assert reg.tier_ladder_is_degenerate()


def test_a_REAL_ladder_is_not_reported_degenerate() -> None:
    """The control. A check that always says "degenerate" would be worthless the
    moment a second model is wired, and nobody would notice it had stopped
    meaning anything."""
    reg = _registry_with({
        "small": [("qwen3.6:8b", ["fast"])],
        "big": [("neraai-v1-raw", ["standard", "powerful"])],
    })

    ladder = reg.describe_tier_ladder()
    assert ladder["fast"] != ladder["powerful"]
    assert not reg.tier_ladder_is_degenerate()


def test_two_providers_running_the_SAME_MODEL_are_still_degenerate() -> None:
    """The exact trap in the live config: three disabled providers with different
    NAMES and tier labels, all naming qwen3.6:35b on one host. Different names do
    not make a ladder; different models do."""
    reg = _registry_with({
        "a": [("qwen3.6:35b", ["fast"])],
        "b": [("qwen3.6:35b", ["standard"])],
        "c": [("qwen3.6:35b", ["powerful"])],
    })

    assert reg.tier_ladder_is_degenerate(), (
        "three names for one model was read as a three-rung ladder"
    )


def test_boot_REPORTS_the_ladder() -> None:
    """A report nothing calls is the shape this whole item is about."""
    from stackowl.startup import orchestrator

    src = inspect.getsource(orchestrator)
    assert "describe_tier_ladder()" in src
    assert "tier ladder resolved" in src
