"""D01.2 — ``cache_ttl`` is a PER-PROVIDER economic decision.

It sits on ProviderConfig alongside ``context_chars`` and ``max_output_tokens``
rather than in a new top-level section, so a user running two Anthropic backends
can price them differently. There is no enable/disable flag: the capability gate
already makes marking inert on every non-Anthropic provider, so the feature ships
ON per the standing rule instead of behind a setting that defaults off forever.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from stackowl.config.provider import ProviderConfig


def _config(**overrides: object) -> ProviderConfig:
    base: dict[str, object] = {
        "name": "anthropic-main",
        "protocol": "anthropic",
        "default_model": "claude-opus-5",
        "tiers": ("powerful",),
    }
    base.update(overrides)
    return ProviderConfig(**base)  # type: ignore[arg-type]


def test_cache_ttl_defaults_to_five_minutes() -> None:
    """5m is the default because a conversation is a burst of turns minutes apart.

    1h doubles the write cost (2x vs 1.25x) and needs 3+ reads to break even, so
    a conversation that stops after two turns would become MORE expensive than
    not caching at all.
    """
    assert _config().cache_ttl == "5m"


def test_cache_ttl_accepts_one_hour() -> None:
    assert _config(cache_ttl="1h").cache_ttl == "1h"


def test_cache_ttl_rejects_an_unsupported_value() -> None:
    """Anthropic offers exactly two TTLs. A typo must fail at config load, not
    silently ship a marker the API rejects on the first real request."""
    with pytest.raises(ValidationError):
        _config(cache_ttl="30m")


def test_cache_ttl_is_independent_per_provider() -> None:
    """Two backends, two economics — the reason this is not a global setting."""
    assert _config(name="a").cache_ttl == "5m"
    assert _config(name="b", cache_ttl="1h").cache_ttl == "1h"
