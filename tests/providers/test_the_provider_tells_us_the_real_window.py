"""The window was wrong by 4x and the provider said so, five separate times.

LIVE FAILURE 2026-09-01T21:56:19Z. Bakir sent "Hey" — three characters — and got
"I apologize, but I can't complete your request right now." The platform's own
budget line for that turn::

    model_window: 1000000  system_prompt_tokens: 3757  history_tokens: 1166
    tools_count: 79  tools_tokens: 19572  total_est_tokens: 24495

24,495 tokens. The provider rejected it with::

    ContextWindowExceededError - This model's maximum context length is 262144 tokens

Nothing was too big. ``DEFAULT_WINDOW_FALLBACK`` is 1,000,000 and the model's real
window is 262,144, so ``_output_cap`` sized ``max_tokens`` against a window four
times too large and input + output blew the real ceiling. Measured across 68
context-budget records: 39 turns saw the true 262,144 (the probe worked), 22 saw
None, and 7 saw 1,000,000 — his was one of the 7.

WHY A FIFTH PATCH TO ``_output_cap`` WOULD BE THE WRONG FIX. That function
already carries four dated repairs for this exact 400 — 2026-07-18, 07-21, 07-22,
07-23 — each making the input ESTIMATE more accurate (bound by max_output_tokens,
then reserve headroom, then a safety margin, then count tool schemas). The
estimate is a heuristic, not the tokenizer, so it can always be wrong by a little;
but none of that mattered here, because the WINDOW itself was wrong by 738k. The
recurring shape is not a bad estimate. It is that the platform's belief about the
window is never corrected by the only authority on it.

THE PROVIDER IS THAT AUTHORITY AND IT SAYS THE NUMBER OUT LOUD. Every one of the
eight recorded rejections carries the real limit in its message, and nothing read
it — so the same wrong window was used again on the next turn, forever.

THE OWNER'S DECISION IS NOT TOUCHED. DEFAULT_WINDOW_FALLBACK stays 1,000,000
(raised deliberately on 2026-07-22: "probing genuinely failing should assume a
large modern-context model"). This makes that choice survivable rather than
overriding it — whatever the fallback, the first rejection replaces it with the
truth, and the correction is remembered.

IT ONLY EVER SHRINKS. A parsed window is accepted only when it is SMALLER than
what is cached. A rejection can prove a window is too small to hold the request;
it can never prove one is larger, and trusting an error text to RAISE a bound is
how a parse bug would turn into an outage.
"""

from __future__ import annotations

import logging

from stackowl.providers import model_window
from stackowl.providers.model_window import (
    cached_window,
    learn_window_from_error,
)

_LIVE_ERROR = (
    "Error code: 400 - {'error': {'message': \"litellm.ContextWindowExceededError: "
    "litellm.BadRequestError: ContextWindowExceededError - This model's maximum "
    "context length is 262144 tokens. However, you requested 981234 tokens.\"}}"
)

_P, _M = "TestProvider", "test-model"


def _reset() -> None:
    model_window._WINDOW_CACHE.pop((_P, _M), None)  # noqa: SLF001


def test_the_real_window_is_learned_from_the_live_rejection() -> None:
    """The exact text of the message that failed his "Hey"."""
    _reset()
    model_window._WINDOW_CACHE[(_P, _M)] = 1_000_000  # noqa: SLF001
    assert learn_window_from_error(_P, _M, _LIVE_ERROR) == 262144
    assert cached_window(_P, _M) == 262144, (
        "the correction was not remembered — the next turn repeats the failure, "
        "which is what happened eight times"
    )


def test_it_only_ever_SHRINKS_the_window() -> None:
    """The expensive direction. A rejection proves a window is too small to hold
    the request; it can never prove one is LARGER. Trusting an error text to raise
    a bound turns a parse bug into an outage."""
    _reset()
    model_window._WINDOW_CACHE[(_P, _M)] = 8192  # noqa: SLF001
    assert learn_window_from_error(_P, _M, _LIVE_ERROR) is None
    assert cached_window(_P, _M) == 8192


def test_an_unrelated_400_teaches_nothing() -> None:
    """A malformed-tool-call 400, an auth failure, a rate limit — none of them
    carry a window, and inventing one from an unrelated error would be worse than
    the bug."""
    _reset()
    model_window._WINDOW_CACHE[(_P, _M)] = 1_000_000  # noqa: SLF001
    for text in (
        "Error code: 400 - invalid 'tools[3].function.name'",
        "Error code: 401 - incorrect api key provided",
        "Error code: 429 - rate limit reached for requests",
        "",
    ):
        assert learn_window_from_error(_P, _M, text) is None
    assert cached_window(_P, _M) == 1_000_000


def test_a_nonsense_number_is_refused() -> None:
    """Defensive: a zero, a negative, or an absurd value must not become the
    platform's belief about the window."""
    _reset()
    model_window._WINDOW_CACHE[(_P, _M)] = 262144  # noqa: SLF001
    for bad in ("maximum context length is 0 tokens",
                "maximum context length is 12 tokens"):
        assert learn_window_from_error(_P, _M, bad) is None


def test_it_learns_even_with_nothing_cached() -> None:
    """A rejection before any probe resolved must still teach — otherwise the
    very first turn after a probe failure cannot self-correct."""
    _reset()
    assert learn_window_from_error(_P, _M, _LIVE_ERROR) == 262144
    assert cached_window(_P, _M) == 262144


def test_the_correction_is_visible_at_INFO(caplog) -> None:  # noqa: ANN001
    """Production runs at INFO, and this is the evidence that self-healing
    actually happened rather than the same 400 recurring silently."""
    _reset()
    model_window._WINDOW_CACHE[(_P, _M)] = 1_000_000  # noqa: SLF001
    with caplog.at_level(logging.INFO):
        learn_window_from_error(_P, _M, _LIVE_ERROR)
    assert any("learned the real context window" in r.getMessage() for r in caplog.records)


def test_it_never_raises() -> None:
    """A correction path may never be the thing that fails a turn."""
    _reset()
    assert learn_window_from_error(_P, _M, None) is None  # type: ignore[arg-type]
    assert learn_window_from_error(_P, _M, object()) is None  # type: ignore[arg-type]


def test_the_probe_failure_floor_fails_SAFE() -> None:
    """A bound that errs optimistically does not degrade — it FAILS.

    Bakir lowered this from 1,000,000 to 100,000 on 2026-09-01 after the live
    incident: an over-estimate made `_output_cap` size max_tokens against a window
    four times the real one and the provider rejected the request outright. An
    under-estimate only wastes capacity. The floor must therefore stay BELOW every
    window this platform has met — the smallest real one on record is 262,144."""
    assert model_window.DEFAULT_WINDOW_FALLBACK == 100_000
    assert model_window._CLOUD_DEFAULT == 100_000  # noqa: SLF001
    assert model_window.DEFAULT_WINDOW_FALLBACK < 262_144, (
        "the probe-failure floor is at or above a real model window — it can now "
        "over-estimate again, which is the failure this value exists to prevent"
    )
