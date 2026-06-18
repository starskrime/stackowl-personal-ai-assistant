"""PARL-1 (F078, F079) — error/timeout sentinels never count as owl positions.

* Truncated / error-marker responses are excluded from the convergence embedding
  call and from synthesis.
* If fewer than 2 GENUINE responses survive, convergence returns False WITHOUT
  ever calling the embedding provider.
* The token-budget estimate is token-aware (multilingual), not naive char/4.
"""

from __future__ import annotations

import pytest

from stackowl.parliament.convergence import ConvergenceDetector
from stackowl.parliament.models import ParliamentRound
from stackowl.parliament.token_estimate import estimate_tokens


class _SpyProvider:
    def __init__(self) -> None:
        self.embed_calls: list[list[str]] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.embed_calls.append(list(texts))
        # Identical unit vectors → mean cosine 1.0 for whatever survives.
        return [[1.0, 0.0, 0.0] for _ in texts]


class _SpyRegistry:
    def __init__(self, provider: _SpyProvider) -> None:
        self._provider = provider

    def get(self) -> _SpyProvider:
        return self._provider


def _round(responses: dict[str, str], truncated: dict[str, bool]) -> ParliamentRound:
    return ParliamentRound(round_number=1, responses=responses, truncated=truncated)


@pytest.mark.asyncio
async def test_sentinels_excluded_from_embedding() -> None:
    provider = _SpyProvider()
    det = ConvergenceDetector(threshold=0.85, embedding_registry=_SpyRegistry(provider))  # type: ignore[arg-type]
    rnd = _round(
        {"scout": "real answer one", "sage": "real answer two", "owl": "[error: TimeoutError]"},
        {"scout": False, "sage": False, "owl": True},
    )
    converged = await det.check(rnd)
    # Exactly the 2 genuine responses were embedded; the sentinel was dropped.
    assert provider.embed_calls == [["real answer one", "real answer two"]]
    assert converged is True


@pytest.mark.asyncio
async def test_fewer_than_two_genuine_skips_embedding_call() -> None:
    provider = _SpyProvider()
    det = ConvergenceDetector(threshold=0.85, embedding_registry=_SpyRegistry(provider))  # type: ignore[arg-type]
    rnd = _round(
        {"scout": "only real one", "sage": "[timed out after 30s]", "owl": "[error: X]"},
        {"scout": False, "sage": True, "owl": True},
    )
    converged = await det.check(rnd)
    assert converged is False
    # No embedding provider call at all — short-circuit on <2 genuine.
    assert provider.embed_calls == []


def test_genuine_responses_filters_truncated() -> None:
    rnd = _round(
        {"a": "keep me", "b": "[error: Z]", "c": "keep too"},
        {"a": False, "b": True, "c": False},
    )
    assert rnd.genuine_responses() == {"a": "keep me", "c": "keep too"}


def test_token_estimate_is_multilingual_not_char_div_four() -> None:
    # A CJK string has ~1 token per char in practice — char/4 grossly undercounts.
    cjk = "日本語のテキストです"  # 10 chars, no spaces
    assert estimate_tokens(cjk) > len(cjk) // 4
    # An English/Latin string is estimated on word+punct boundaries, not bytes.
    en = "the quick brown fox jumps"
    assert estimate_tokens(en) >= 5  # at least one token per word
    # Empty string → 0 tokens.
    assert estimate_tokens("") == 0
