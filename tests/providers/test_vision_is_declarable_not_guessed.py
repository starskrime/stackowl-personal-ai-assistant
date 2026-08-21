"""An operator can TELL the platform a backend sees images.

MEASURED ON THE LIVE DEPLOYMENT 2026-08-20, and this is the whole justification:

    8 distinct models ever run, 99,573 recorded calls
      False   65,987  neraai-v1-raw
      False   28,763  qwen3.6:35b
      False    2,688  qwen3.5:122b
      False    1,255  qwen3.5:2b
      False      443  gemma4:12b-mlx
      False      229  gemma4:31b-mlx
      False      148  qwen3.6:35b-mlx
      False       60  qwen3.5:9b-mlx
    calls recognised as vision-capable: 0 / 99,573

``is_vision_model`` matches a model id against 33 hardcoded vendor-family
substrings. The list has ``gemma3``; the box runs **gemma4**. It has ``qwen2-vl``;
the box runs **qwen3.5** and **qwen3.6**. The primary backend is a private gateway
model named ``neraai-v1-raw``, which no vendor list can ever describe.

So vision is not degraded, it is UNREACHABLE — `VisionSelector.select()` can never
return a provider, which takes `vision_analyze`, `browser_vision` and GUI vision
routing with it. The transport is fine: the OpenAI image path builds a proper
``image_url`` block (``providers/_blocks.py``). Only the RECOGNITION fails.

WHY A DECLARATION IS THE RIGHT FIRST RUNG, AND NOT A LONGER LIST. The standing rule
is to prefer dynamic discovery over hardcoded config — and a substring list is not
discovery, it is a hardcoded guess wearing discovery's clothes. It also breaks two
other standing rules outright: no hardcoded keyword lists, and no vendor names in
``src/``. The rule forbids guessing what the system KNOWS; it does not forbid the
system being TOLD. A private gateway model's capabilities are not discoverable from
its name by anyone, which is exactly the case where a declaration is more honest
than inference.

This is rung one of the ladder ``model_window.py`` already proves works — operator
override first, then probe, then catalog, then a conservative default. The token
list stays as the LAST rung for now: deleting it before the probe exists would
regress deployments whose model names it does describe.
"""

from __future__ import annotations

import pytest  # noqa: F401

from stackowl.config.provider import ModelOverride, ProviderConfig


def _cfg(**over: object) -> ProviderConfig:
    base: dict = {
        "name": "NeraAiRaw", "protocol": "openai",
        "default_model": "neraai-v1-raw", "tiers": ("fast",),
    }
    base.update(over)
    return ProviderConfig(**base)  # type: ignore[arg-type]


class TestTheOperatorCanDeclareIt:
    def test_a_declared_true_wins_over_an_unrecognised_name(self) -> None:
        """The exact live case: a private gateway model no list can describe."""
        assert _cfg(supports_vision=True).resolve_vision() is True

    def test_a_declared_false_is_the_only_way_to_turn_it_off(self) -> None:
        """The override exists for the rare backend that genuinely cannot. It is an
        OPT-OUT, never an opt-in — the operator sets nothing in the normal case."""
        assert _cfg(default_model="llava", supports_vision=False).resolve_vision() is False
        assert _cfg(supports_vision=False).resolve_vision() is False

    def test_unset_means_ENABLED_not_guessed(self) -> None:
        """BAKIR'S STANDING RULE, 2026-08-20: "make it default, never ask me to enable
        anything, everything should be enabled at system level."

        So silence means CAPABLE, for every backend, whatever its model is named. This
        is what let the 33-token vendor list be deleted outright: it recognised 0 of
        99,573 real calls, so it was never a fallback anyone could rely on — and
        "assume capable, let a real failure say otherwise" is the same doctrine the
        rest of the platform runs on. Measure the EFFECT; never infer the capability.
        """
        assert _cfg(default_model="llava").resolve_vision() is True
        assert _cfg(default_model="neraai-v1-raw").resolve_vision() is True
        assert _cfg(default_model="anything-at-all").resolve_vision() is True

    def test_none_is_the_default_so_no_config_changes_meaning(self) -> None:
        assert ProviderConfig.model_fields["supports_vision"].default is None


class TestPerModelDeclaration:
    def test_a_model_override_can_declare_its_own_vision(self) -> None:
        """One connection can host several models (`ModelOverride`), and they need not
        agree — a gateway may front both a text model and a multimodal one."""
        cfg = _cfg(
            supports_vision=False,
            models=(ModelOverride(name="sees", tiers=("fast",), supports_vision=True),),
        )
        assert cfg.resolve_vision("sees") is True
        assert cfg.resolve_vision("neraai-v1-raw") is False

    def test_a_model_override_inherits_when_unset(self) -> None:
        """`None` on an override means inherit the parent, the same contract
        max_output_tokens and context_chars already use."""
        cfg = _cfg(
            supports_vision=True,
            models=(ModelOverride(name="quiet", tiers=("fast",)),),
        )
        assert cfg.resolve_vision("quiet") is True

    def test_an_unknown_model_name_uses_the_parent(self) -> None:
        """Never raise on a model the config has no entry for — a capability check
        that throws would take the turn with it."""
        assert _cfg(supports_vision=True).resolve_vision("not-configured") is True


class TestTheProvidersActuallyConsultIt:
    """Built through the REAL constructor signature — `OpenAIProvider(config, api_key)`.

    The first draft of these two passed `config` alone and died on a TypeError, which
    is this programme's second recurring defect caught on itself: a double that stopped
    resembling the real thing. A fixture that cannot construct the object cannot prove
    the object reads anything.
    """

    def test_the_openai_provider_reports_a_declared_capability(self) -> None:
        """The declaration is worthless unless the provider reads it. This is the seam
        `VisionSelector` filters on, so it is the one that decides whether vision is
        reachable at all."""
        from stackowl.providers.openai_provider import OpenAIProvider

        p = OpenAIProvider(_cfg(supports_vision=True), "k")
        assert p.supports_vision is True

    def test_the_openai_provider_defaults_to_capable(self) -> None:
        """Undeclared reaches the provider as CAPABLE, which is what makes vision
        reachable on this deployment without the operator touching anything."""
        from stackowl.providers.openai_provider import OpenAIProvider

        assert OpenAIProvider(_cfg(default_model="llava"), "k").supports_vision is True
        assert OpenAIProvider(_cfg(), "k").supports_vision is True
        assert OpenAIProvider(_cfg(supports_vision=False), "k").supports_vision is False
