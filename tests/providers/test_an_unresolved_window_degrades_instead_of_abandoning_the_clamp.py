"""An unresolved window is "not known YET", not "no limit".

THE LIVE FAILURE. MEASURED 2026-09-09 across every retained log: **21 identical
records** of

    "requested 250000 output tokens and your prompt contains at least 12145 input
     tokens, for a total of at least 262145 tokens"

against a 262,144-token window — over by exactly ONE token, the request rejected
outright with a 400. 250000 is `max_output_tokens` VERBATIM, so nothing shaped
that request at all.

WHY IT SURVIVED A FIX AIMED AT IT. `_output_cap` reads the window with
`cached_window`, which answers only the PROBE tier and returns `None` on a miss —
and the `None` branch returned `effective_max_output_tokens` unchanged. So the one
path with the LEAST information was the only one that abandoned the clamp instead
of degrading. `DEFAULT_WINDOW_FALLBACK` was lowered 1,000,000 -> 100,000 on
2026-09-01 (owner decision) for precisely this 400 — and FIVE MORE happened on
2026-09-08, because this branch never consulted a fallback at all. The fallback's
VALUE was corrected one tier above the path that produced the failure.

AND THE TIER IT NEEDED WAS ALREADY RESOLVED AND THROWN AWAY. `model_window`'s
module docstring states the precedence — config `context_chars` -> probe -> known
default -> conservative fallback — and `window_from_config` has existed for the
config tier since it was written. Its only mention in the whole tree was a
throwaway `_effective_context_chars` binding on the line above this branch: the
value was computed, named with a leading underscore, and discarded. Built, and not
wired to the one case that needed it.
"""

from __future__ import annotations

import pytest

from stackowl.config.provider import ProviderConfig
from stackowl.providers.model_window import DEFAULT_WINDOW_FALLBACK
from stackowl.providers.openai_provider import OpenAIProvider

#: The real window of the deployment that produced the 21 records.
_REAL_WINDOW = 262_144
#: The configured ceiling those requests asked for whole.
_CEILING = 250_000
#: ~12,145 tokens, the input size in every one of the 21 records (~4 chars/token).
_LIVE_PROMPT_CHARS = 48_580


class _Msg:
    def __init__(self, text: str) -> None:
        self.content = text
        self.role = "user"


def _cap(monkeypatch, *, window: int | None, context_chars: int | None = None) -> int:
    """`_output_cap` with the window tier forced to *window* (None = a cache miss)."""
    import stackowl.providers.model_window as mw

    monkeypatch.setattr(mw, "cached_window", lambda *_a, **_k: window)
    cfg = ProviderConfig(
        name="NeraAiRaw", protocol="openai", default_model="neraai-v1-raw",
        tiers=("fast",), base_url="http://gw/v1", max_output_tokens=_CEILING,
        context_chars=context_chars,
    )
    provider = OpenAIProvider(cfg, "k")
    return provider._output_cap(  # noqa: SLF001 — the unit under test
        "neraai-v1-raw", [_Msg("x" * _LIVE_PROMPT_CHARS)]
    )


@pytest.mark.tripwire
def test_an_unresolved_window_does_not_request_the_raw_ceiling(monkeypatch) -> None:
    """THE REGRESSION, at the exact size that produced the 400s."""
    cap = _cap(monkeypatch, window=None)
    assert cap < _CEILING, (
        "a cache miss still hands back max_output_tokens whole — the branch with "
        "the least information is again the only one that does not degrade"
    )
    assert cap + 12_145 <= DEFAULT_WINDOW_FALLBACK, (
        "the request would exceed even the conservative fallback window it is "
        f"supposed to be budgeting against: cap={cap}"
    )


@pytest.mark.tripwire
def test_the_live_arithmetic_can_no_longer_recur(monkeypatch) -> None:
    """cap + input must never reach the REAL window either, whichever tier answered."""
    for window in (None, _REAL_WINDOW):
        cap = _cap(monkeypatch, window=window)
        assert cap + 12_145 < _REAL_WINDOW, (
            f"window={window}: cap {cap} + 12,145 input = {cap + 12_145} against a "
            f"{_REAL_WINDOW} window — this is the 262,145 that was rejected 21 times"
        )


def test_the_configured_context_chars_is_actually_consulted(monkeypatch) -> None:
    """THE WIRING, proven by DIFFERENCE rather than by reading the source.

    If `context_chars` were still discarded, both calls would return the same
    number — the fallback's. They must not.
    """
    from_fallback = _cap(monkeypatch, window=None)
    from_config = _cap(monkeypatch, window=None, context_chars=1_000_000)
    assert from_config != from_fallback, (
        "the configured context_chars changes nothing, so it is still being thrown "
        "away and only the fallback tier is live"
    )
    assert from_config > from_fallback, (
        "a LARGER configured context should permit a larger answer, not a smaller one"
    )


def test_a_resolved_window_is_completely_unaffected(monkeypatch) -> None:
    """THE CONTROL. This change must touch only the miss path; if the normal path
    moved, the fix is a behaviour change wearing a bug fix's clothes."""
    assert _cap(monkeypatch, window=_REAL_WINDOW) == 229_376


def test_the_test_can_actually_fail(monkeypatch) -> None:
    """VACUITY CONTROL — the assertions above are all upper bounds, which a cap of
    zero would satisfy trivially. Require a USABLE budget too."""
    for window in (None, _REAL_WINDOW):
        assert _cap(monkeypatch, window=window) > 1_000, (
            "the cap collapsed to something unusable; the bounds above would still "
            "pass, which is the 0-over-0 shape this repo keeps paying for"
        )
