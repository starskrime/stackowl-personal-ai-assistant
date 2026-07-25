"""D01.6 turn metrics — one test per stated invariant.

Covers the two units added by D01.6: the prompt-identity carrier
(``infra/prompt_metrics.py``) and the provider-side cached-token reader
(``providers/openai_provider._cached_input_tokens``).

Invariants under test are the ones written in
``docs/hermes-mapping/designs/D01.6.md``. Each test names the invariant it
guards so a failure says which contract broke, not merely which line.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from stackowl.infra import prompt_metrics
from stackowl.providers.openai_provider import _cached_input_tokens


@pytest.fixture(autouse=True)
def _clean_carrier():
    prompt_metrics.reset()
    yield
    prompt_metrics.reset()


# --------------------------------------------------------------------------
# I2 — the hash is computed over the exact string that will be sent.
# --------------------------------------------------------------------------

def test_i2_digest_is_stable_for_identical_prompts() -> None:
    """The same prompt always digests to the same value — this is what makes
    'byte-identical across turns' a checkable claim rather than an assertion."""
    assert prompt_metrics.digest("system prompt") == prompt_metrics.digest("system prompt")


def test_i2_digest_differs_on_any_change() -> None:
    """A single trailing space is a different prompt and must digest differently,
    otherwise D01.1's stability invariant would pass on a prompt that churns."""
    assert prompt_metrics.digest("system prompt") != prompt_metrics.digest("system prompt ")


def test_i2_absent_prompt_digests_to_empty_not_to_a_hash_of_empty() -> None:
    """A turn with no system prompt has no prompt identity. Hashing "" would
    produce a real-looking digest and make 'no prompt' indistinguishable from a
    prompt that happened to be empty."""
    assert prompt_metrics.digest(None) == ""
    assert prompt_metrics.digest("") == ""


# --------------------------------------------------------------------------
# I5 — only a digest and a length ever leave this module.
# --------------------------------------------------------------------------

def test_i5_digest_does_not_contain_prompt_text() -> None:
    secret = "the user's home address is 12 Somewhere Lane"
    out = prompt_metrics.digest(secret)
    assert secret not in out
    assert len(out) == 16
    assert all(c in "0123456789abcdef" for c in out)


# --------------------------------------------------------------------------
# Carrier round-trip, and the honest 'not measured' value.
# --------------------------------------------------------------------------

def test_stamp_then_current_round_trips() -> None:
    stamped_hash, stamped_chars = prompt_metrics.stamp("hello world")
    assert prompt_metrics.current() == (stamped_hash, stamped_chars)
    assert stamped_chars == len("hello world")


def test_unstamped_turn_reports_not_measured() -> None:
    """A turn that never reached assemble (slash command, health probe) genuinely
    has no prompt identity, and must not be confused with one that hashed."""
    assert prompt_metrics.current() == ("", 0)


def test_carrier_does_not_leak_between_async_contexts() -> None:
    """Concurrent turns must not read each other's prompt identity — the whole
    reason this is a ContextVar rather than a module global."""

    async def _turn(prompt: str) -> tuple[str, int]:
        prompt_metrics.stamp(prompt)
        await asyncio.sleep(0)  # force a scheduling point between the two turns
        return prompt_metrics.current()

    async def _both() -> list[tuple[str, int]]:
        return await asyncio.gather(_turn("turn A"), _turn("turn B"))

    a, b = asyncio.run(_both())
    assert a[0] == prompt_metrics.digest("turn A")
    assert b[0] == prompt_metrics.digest("turn B")
    assert a != b


# --------------------------------------------------------------------------
# I4 — cached_input_tokens = 0 is ambiguous, and the reader never raises.
# --------------------------------------------------------------------------

def test_i4_openai_shape_is_read() -> None:
    usage = SimpleNamespace(prompt_tokens_details=SimpleNamespace(cached_tokens=1024))
    assert _cached_input_tokens(usage) == 1024


def test_i4_openai_shape_as_dict_is_read() -> None:
    """Some gateways hand back a plain dict for prompt_tokens_details."""
    usage = SimpleNamespace(prompt_tokens_details={"cached_tokens": 512})
    assert _cached_input_tokens(usage) == 512


def test_i4_anthropic_style_naming_is_read() -> None:
    """LiteLLM-style gateways pass Anthropic's naming through verbatim, and
    NeraAiRaw is exactly such a gateway — this is not a hypothetical shape."""
    usage = SimpleNamespace(cache_read_input_tokens=256)
    assert _cached_input_tokens(usage) == 256


def test_i4_flattened_variant_is_read() -> None:
    usage = SimpleNamespace(cached_tokens=64)
    assert _cached_input_tokens(usage) == 64


def test_i4_priority_openai_shape_wins_over_flattened() -> None:
    usage = SimpleNamespace(
        prompt_tokens_details=SimpleNamespace(cached_tokens=1000),
        cached_tokens=1,
    )
    assert _cached_input_tokens(usage) == 1000


@pytest.mark.parametrize(
    "usage",
    [
        None,
        SimpleNamespace(),
        SimpleNamespace(prompt_tokens=10, completion_tokens=5),
        SimpleNamespace(prompt_tokens_details=None),
        SimpleNamespace(cached_tokens=None),
        "not a usage object at all",
        object(),
    ],
)
def test_i4_silent_or_odd_provider_yields_zero_and_never_raises(usage: object) -> None:
    """A backend that reports nothing must produce 0, not an exception. Cost
    recording can never break a completion that already happened (B5)."""
    assert _cached_input_tokens(usage) == 0


def test_i1_a_broken_usage_object_still_yields_zero() -> None:
    """Measurement must not become an outage (I1). A usage object whose attribute
    access raises is the worst realistic case."""

    class Exploding:
        @property
        def prompt_tokens_details(self):  # noqa: ANN202 — deliberately hostile
            raise RuntimeError("provider SDK changed shape under us")

    assert _cached_input_tokens(Exploding()) == 0
