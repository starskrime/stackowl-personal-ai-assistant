"""D02.6 — the COMPRESS actuator.

Bakir, 2026-08-05: "one of the biggest rules on each implementation is Agentic
Self Healing. It is a must to remember and always be as guide star."

D02.6 originally shipped RecoveryAction.COMPRESS with NOTHING able to perform it,
and I filed that as debt. A taxonomy that names a recovery it cannot carry out is
a diagnosis with no treatment. These tests exist to prove the treatment works —
and, just as importantly, that it stays bounded and honest when it cannot.
"""

from __future__ import annotations

import pytest

from stackowl.exceptions import ProviderError
from stackowl.providers.base import _MAX_COMPRESS_ATTEMPTS, ModelProvider


class _TooLarge(Exception):
    status_code = 413


class _Unauthorized(Exception):
    status_code = 401


class _Provider(ModelProvider):
    """Minimal concrete provider — the base class owns the actuator."""

    @property
    def name(self) -> str:
        return "test-provider"

    @property
    def protocol(self):  # type: ignore[override]
        return "openai"

    async def complete(self, *a, **k): ...          # pragma: no cover
    async def complete_with_tools(self, *a, **k): ...  # pragma: no cover
    async def stream(self, *a, **k): ...            # pragma: no cover
    async def health_check(self, *a, **k): ...      # pragma: no cover


@pytest.fixture
def provider():
    return _Provider()


async def test_a_413_is_retried_against_a_smaller_payload(provider):
    """The whole point: the turn survives an oversize rejection."""
    seen: list[int] = []

    async def fail_at(size: int):
        seen.append(size)
        if size > 500:
            raise ProviderError("p", cause=_TooLarge())
        return f"ok@{size}"

    sizes = iter([400])

    def shrink(attempt: int):
        nxt = next(sizes)
        return lambda: fail_at(nxt)

    result = await provider._resilient_round(lambda: fail_at(1000), shrink=shrink)
    assert result == "ok@400"
    assert seen == [1000, 400], "should have retried exactly once, smaller"


async def test_shrinking_is_bounded(provider):
    """A provider that rejects EVERY size must not loop forever. This is the
    failure mode that makes a self-healing loop worse than no loop at all."""
    attempts = 0

    async def always_too_large():
        nonlocal attempts
        attempts += 1
        raise ProviderError("p", cause=_TooLarge())

    with pytest.raises(ProviderError):
        await provider._resilient_round(
            always_too_large, shrink=lambda _a: always_too_large
        )
    assert attempts == _MAX_COMPRESS_ATTEMPTS + 1, "one initial call + bounded retries"


async def test_a_shrink_that_cannot_shrink_surfaces_honestly(provider):
    """Returning None means 'nothing left to give'. The error must propagate
    rather than be retried identically — a retry that changes nothing is a
    fake recovery, which is exactly what the guide-star rule forbids."""
    calls = 0

    async def too_large():
        nonlocal calls
        calls += 1
        raise ProviderError("p", cause=_TooLarge())

    with pytest.raises(ProviderError):
        await provider._resilient_round(too_large, shrink=lambda _a: None)
    assert calls == 1, "must not call again when there is nothing to compress"


async def test_only_a_payload_error_triggers_compression(provider):
    """A 401 is not fixed by sending less. Compressing it would burn the turn's
    budget hiding a credential problem."""
    shrink_calls = 0

    async def unauthorized():
        raise ProviderError("p", cause=_Unauthorized())

    def shrink(_a):
        nonlocal shrink_calls
        shrink_calls += 1
        return unauthorized

    with pytest.raises(ProviderError):
        await provider._resilient_round(unauthorized, shrink=shrink)
    assert shrink_calls == 0


async def test_without_a_shrink_callback_behaviour_is_unchanged(provider):
    """Every existing call site passes no shrink. They must be byte-identical."""
    async def too_large():
        raise ProviderError("p", cause=_TooLarge())

    with pytest.raises(ProviderError):
        await provider._resilient_round(too_large)


# --------------------------------------------------------------------------- #
# Healing, not just recovering.
# --------------------------------------------------------------------------- #


def test_a_learned_limit_lowers_the_budget_for_later_calls(provider):
    """Recovering from the same rejection every round is not healing — it is
    paying the same toll repeatedly. The provider must stop re-entering it."""
    assert provider._effective_context_budget(1_000_000) == 1_000_000
    provider.note_payload_limit(250_000)
    assert provider._effective_context_budget(1_000_000) == 250_000


def test_a_learned_limit_only_ever_moves_down(provider):
    """A later, larger success must not raise the ceiling back up — that would
    walk straight back into the rejection it just learned to avoid."""
    provider.note_payload_limit(250_000)
    provider.note_payload_limit(900_000)
    assert provider._effective_context_budget(1_000_000) == 250_000
    provider.note_payload_limit(100_000)
    assert provider._effective_context_budget(1_000_000) == 100_000


def test_the_learned_limit_never_raises_a_smaller_configured_budget(provider):
    """Config still wins when it is stricter."""
    provider.note_payload_limit(500_000)
    assert provider._effective_context_budget(80_000) == 80_000


def test_a_nonsense_limit_is_ignored(provider):
    """A 0 or negative would pin the budget at nothing and silently end every
    later turn — fail safe rather than fail closed."""
    provider.note_payload_limit(0)
    provider.note_payload_limit(-5)
    assert provider._effective_context_budget(1_000_000) == 1_000_000
