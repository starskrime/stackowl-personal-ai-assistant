"""Every provider that accepts images also accepts document blocks.

THE VISION DEFECT, ONE PROPERTY OVER. On 2026-08-20 `supports_vision` was fixed by
deleting a vendor-substring list that had recognised 0 of 99,573 real calls and
defaulting the capability to ENABLED, resolved through `ProviderConfig.resolve_vision()`.
Anthropic and Gemini were given BOTH properties off that one resolver
(`anthropic_provider.py:179/184`, `gemini_provider.py:117/122`). `OpenAIProvider` got
`supports_vision` (`:386`) and **not** `supports_document`, so it fell through to the
ABC default of False (`base.py:382`).

MEASURED 2026-08-21. Every backend in `~/.stackowl/stackowl.yaml` is `protocol: openai`,
and `registry._build_provider` maps every non-anthropic, non-gemini protocol to
`OpenAIProvider`. So:

    pdf.py:209   provider = next((p for p in registry.all() if p.supports_document), None)

could only ever evaluate to `None`, and Mode B answered "no configured provider supports
document input" — a capability that cannot be switched on, with no config key that fixes
it, which is precisely what the 2026-08-20 rule ("everything ships ENABLED; an operator
switch is an OPT-OUT only") exists to prevent.

HONEST SCOPE: latent, not observed. The "no document-capable provider" line has fired 0
times against 16 `pdf` invocations — all 16 took Mode A (text extraction), so no user has
hit it yet. It is unreachable, not broken-in-flight.

THE FIX IS THE SMALLER ONE. Not a new `supports_document` config field — the platform
already carries one resolver and two consumers of it, and a second field would be a
second copy of one rule (defect shape #3). `OpenAIProvider` simply gains the property its
siblings already have. An image and a PDF both ride `DocumentBlock`; the two capabilities
were never independent.
"""

from __future__ import annotations

import pytest

from stackowl.config.provider import ProviderConfig
from stackowl.providers.anthropic_provider import AnthropicProvider
from stackowl.providers.openai_provider import OpenAIProvider


def _config(**over: object) -> ProviderConfig:
    base: dict[str, object] = {
        "name": "NeraAiRaw", "protocol": "openai",
        "default_model": "neraai-v1-raw", "tiers": ("fast",),
        "base_url": "http://gw/v1",
    }
    base.update(over)
    return ProviderConfig(**base)  # type: ignore[arg-type]


class TestTheOpenAIProviderCanCarryADocument:
    def test_it_reports_document_capable_by_default(self) -> None:
        """THE LIVE CASE. Every configured backend is protocol openai, so this
        property alone decides whether pdf Mode B can ever run."""
        assert OpenAIProvider(_config(), "k").supports_document is True

    def test_the_operator_can_still_OPT_OUT(self) -> None:
        """Enabled by default is not the same as unconditional. `supports_vision:
        false` must turn both off together — they ride one resolver."""
        provider = OpenAIProvider(_config(supports_vision=False), "k")

        assert provider.supports_document is False
        assert provider.supports_vision is False

    def test_document_and_vision_never_disagree(self) -> None:
        """One resolver, one answer. If these ever diverge there are two copies of
        the rule again, which is how the gap opened in the first place."""
        for flag in (True, False, None):
            provider = OpenAIProvider(_config(supports_vision=flag), "k")
            assert provider.supports_document == provider.supports_vision, flag

    def test_a_per_model_override_reaches_it(self) -> None:
        """`resolve_vision()` checks per-model before per-backend. The document
        property must inherit that, not read the backend field directly."""
        cfg = _config(
            supports_vision=True,
            models=(
                {"name": "text-only-v1", "tiers": ("fast",), "supports_vision": False},
            ),
        )

        assert OpenAIProvider(cfg, "k").supports_document is True  # backend default
        assert cfg.resolve_vision("text-only-v1") is False


class TestEveryProviderAnswersFromTheSameResolver:
    """The gap was an ASYMMETRY between sibling classes, so assert the symmetry
    rather than the individual answers."""

    @pytest.mark.parametrize("protocol", ["openai", "anthropic"])
    def test_both_capabilities_come_from_resolve_vision(self, protocol: str) -> None:
        cfg = _config(protocol=protocol, supports_vision=False)
        provider = (
            AnthropicProvider(cfg, "k") if protocol == "anthropic"
            else OpenAIProvider(cfg, "k")
        )

        assert provider.supports_document is cfg.resolve_vision()
        assert provider.supports_vision is cfg.resolve_vision()


class TestThePdfToolCanNowFindOne:
    def test_a_default_openai_backend_satisfies_the_mode_B_selector(self) -> None:
        """Drives `pdf.py:209`'s exact predicate against the real provider, because
        the defect was invisible to every test that did not run that line."""
        providers = [OpenAIProvider(_config(), "k")]

        chosen = next((p for p in providers if p.supports_document), None)

        assert chosen is not None, (
            "pdf Mode B cannot select a provider — the capability is unreachable"
        )
