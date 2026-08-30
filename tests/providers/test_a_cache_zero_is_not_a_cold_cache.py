"""Distinguish "the provider reported no cache hit" from "it reported nothing".

D01.6's metric 1 reads ZERO prefix-cache hits, and that zero is the measurement
Law 1 (per-conversation prompt caching is sacred) ultimately rests on. Re-measured
2026-08-30 over 5x the original data, the zero HOLDS: 0 cached_input_tokens against
654,849,862 input tokens across 124,683 rows, on every model.

But the zero is uninterpretable, and the code says so itself. From
``_cached_input_tokens``::

    "The 0 return is AMBIGUOUS by construction — it means 'no cache hit' OR 'this
    backend does not report cache statistics' (D01.6 invariant I4). Readers must
    count reporting rows to tell those apart; do not read a 0 as a cold cache."

NOTHING LETS A READER DO THAT. The one line that tries has two defects:

    log.engine.debug(                                  # <- DEBUG
        "[cost_tracker] record: cache stats source",
        extra={"_fields": {
            "source": "reported" if cached_input_tokens > 0 else "absent_or_zero",
    ...

  1. It is at DEBUG while production runs at INFO, so in the retained logs it does
     not exist at all — the same failure this repo already paid for once, where an
     acceptance check could never be closed because its only evidence was DEBUG.
  2. It does not actually disambiguate. `cached_input_tokens > 0` answers "was the
     value positive", not "was the field present", so a backend reporting
     `cached_tokens: 0` is recorded identically to one reporting nothing. The exact
     ambiguity the line exists to resolve is baked into its own condition.

PRESENCE IS DETECTABLE — the extractor simply throws it away. It tests `if cached:`
(truthiness), so a present-but-zero field falls through as though absent.

This does not change one token of cost accounting. It records WHETHER the backend
spoke, so I4 becomes satisfiable and D01.6's zero can finally be read as either "the
cache is cold" or "we cannot see the cache".
"""

from __future__ import annotations

from stackowl.providers.openai_provider import (
    _cached_input_tokens,
    cache_stats_reported,
)


class _Details:
    def __init__(self, cached: int | None) -> None:
        if cached is not None:
            self.cached_tokens = cached


class _Usage:
    def __init__(self, **kw: object) -> None:
        for k, v in kw.items():
            setattr(self, k, v)


def test_a_backend_that_reports_a_ZERO_hit_counts_as_reporting() -> None:
    """The case the old condition could not express, and the whole point."""
    usage = _Usage(prompt_tokens_details=_Details(0))
    assert _cached_input_tokens(usage) == 0
    assert cache_stats_reported(usage) is True, (
        "a backend that reported cached_tokens=0 is recorded as silent, so a COLD "
        "cache is indistinguishable from an INVISIBLE one"
    )


def test_a_silent_backend_is_reported_as_silent() -> None:
    """The live case: 654,849,862 input tokens and not one cache field."""
    assert cache_stats_reported(_Usage(prompt_tokens=10)) is False
    assert cache_stats_reported(None) is False


def test_a_real_hit_still_reads_as_a_hit() -> None:
    """The guard must be narrow — the accounting itself is unchanged."""
    usage = _Usage(prompt_tokens_details=_Details(4096))
    assert _cached_input_tokens(usage) == 4096
    assert cache_stats_reported(usage) is True


def test_every_naming_variant_counts_as_reporting() -> None:
    """The extractor walks three shapes; presence must walk the same three, or the
    two disagree and one of them is silently wrong."""
    assert cache_stats_reported(_Usage(cache_read_input_tokens=0)) is True
    assert cache_stats_reported(_Usage(cached_tokens=0)) is True
    assert cache_stats_reported(_Usage(prompt_tokens_details={"cached_tokens": 0})) is True


def test_an_odd_usage_shape_never_raises() -> None:
    """B5 — cost recording must never break a completion that already happened."""
    class _Hostile:
        @property
        def prompt_tokens_details(self):  # noqa: ANN201
            raise RuntimeError("boom")

    assert cache_stats_reported(_Hostile()) is False
    assert _cached_input_tokens(_Hostile()) == 0
