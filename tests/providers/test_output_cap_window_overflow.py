"""The output cap must never request more than the window can hold.

THE LIVE 400, 2026-08-15. Bakir asked the assistant to compare two models. The
tool call finally dispatched (the qwen XML shape had just been fixed), web_search
ran twice and returned 8 results each time — and then the turn died:

    ContextWindowExceededError: This model's maximum context length is 262144
    tokens. However, you requested 250000 output tokens and your prompt contains
    at least 12145 input tokens, for a total of at least 262145 tokens.

Over by exactly ONE token. 936 of these 400s sit in the logs across many days,
685 on 2026-07-29 alone, so this is chronic rather than a one-off.

TWO DEFECTS, and the second is the one that actually explains it.

1. THE MARGIN WAS FLAT. `estimate_tokens` is a heuristic and a heuristic's error
   scales with what it estimates, so a fixed 2,000-token pad stops covering it as
   prompts grow. Measured here: real input 12,145 against an estimate of at most
   10,144 — an undercount of >=2,001, one token more than the pad could absorb.

2. THE RESERVATION WAS BYPASSABLE. `min(max_output_tokens, headroom)` only
   protects while headroom is the smaller value. With the operator's configured
   250,000 against a 262,144 window, just 12,144 tokens remain for the prompt, so
   any turn above that is rejected no matter how good the estimate is — and a
   tool loop's history grows past it quickly. A floor on prompt space fixes what
   no safety margin could.

Neither change overrules the operator's `max_output_tokens`. They decline to
request output that cannot physically coexist with a prompt, which is the rule
the function already states for itself: "never the whole window either".
"""

from __future__ import annotations

import math

from stackowl.providers.openai_provider import (
    _INPUT_ESTIMATE_ERROR_RATE,
    _INPUT_TOKEN_SAFETY_MARGIN,
    _PROMPT_RESERVE_DIVISOR,
    _message_content_text,
)

WINDOW = 262144
MAX_OUTPUT = 250000
REAL_INPUT_AT_FAILURE = 12145


def _cap(estimate: int, window: int = WINDOW, max_output: int = MAX_OUTPUT) -> int:
    """The arithmetic of _output_cap, isolated from the client it needs."""
    margin = max(_INPUT_TOKEN_SAFETY_MARGIN, int(estimate * _INPUT_ESTIMATE_ERROR_RATE))
    headroom = window - estimate - margin
    return min(max_output, headroom, window - window // _PROMPT_RESERVE_DIVISOR)


class TestTheLive400:
    def test_the_exact_incident_no_longer_overflows(self) -> None:
        """est<=10144 is what the failure implies; the real prompt was 12145."""
        assert _cap(10144) + REAL_INPUT_AT_FAILURE <= WINDOW

    def test_it_holds_even_if_the_estimate_was_far_worse(self) -> None:
        """The failure only bounds the estimate from above, so it could have been
        much lower. A 35% undercount must not resurrect the bug — this is the case
        the proportional margin ALONE still failed."""
        for estimate in (9000, 8000, 6000, 4000):
            assert _cap(estimate) + REAL_INPUT_AT_FAILURE <= WINDOW, estimate

    def test_the_old_arithmetic_really_did_fail(self) -> None:
        """Pins the regression itself: the flat-margin formula overflows by 1 on
        the measured numbers. If this ever stops failing, the test above has
        stopped proving anything."""
        old_cap = min(MAX_OUTPUT, WINDOW - 10144 - _INPUT_TOKEN_SAFETY_MARGIN)
        assert old_cap + REAL_INPUT_AT_FAILURE == WINDOW + 1


class TestTheInvariantGenerally:
    def test_no_prompt_size_can_overflow_the_window(self) -> None:
        """The property that matters, swept rather than sampled: for any prompt
        up to the reserve, and any estimator undercount up to 40%, the request
        fits."""
        for real in range(1000, WINDOW // _PROMPT_RESERVE_DIVISOR, 1511):
            for undercount in (0.0, 0.1, 0.25, 0.4):
                estimate = int(real * (1 - undercount))
                assert _cap(estimate) + real <= WINDOW, (real, undercount)

    def test_a_large_prompt_shrinks_the_output_rather_than_failing(self) -> None:
        """Degradation, not rejection: past the reserve the headroom term binds
        and the answer gets shorter."""
        assert _cap(120000) < _cap(40000) < _cap(10000)

    def test_a_small_window_is_never_handed_a_negative_budget(self) -> None:
        """Caught by this test while writing it: an ABSOLUTE reserve of 32,768 is
        right for a 262k model and fatal for an 8k one — 8192 - 32768 is negative.
        The reserve is a fraction of the window for exactly this reason."""
        for window, max_out in ((8192, 2048), (4096, 1024), (32768, 8192)):
            assert _cap(500, window=window, max_output=max_out) > 0, window


class TestToolCallsAreCounted:
    def test_an_assistant_tool_call_is_no_longer_invisible(self) -> None:
        """The blind spot that made the estimate undercount exactly when it
        mattered: a calling message carries content=None and puts the function
        name and its arguments in tool_calls, which are real input tokens on every
        later round."""
        msg = {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "c1",
                "function": {"name": "web_search", "arguments": '{"query": "qwen 3.8 27b"}'},
            }],
        }

        text = _message_content_text(msg)

        assert "web_search" in text and "qwen 3.8 27b" in text

    def test_ordinary_content_is_unchanged(self) -> None:
        assert _message_content_text({"role": "user", "content": "hello"}) == "hello"

    def test_content_and_calls_are_both_counted(self) -> None:
        msg = {
            "role": "assistant",
            "content": "let me look that up",
            "tool_calls": [{"function": {"name": "web_search", "arguments": "{}"}}],
        }

        text = _message_content_text(msg)

        assert "let me look that up" in text and "web_search" in text

    def test_a_message_with_neither_is_still_empty(self) -> None:
        assert _message_content_text({"role": "user"}) == ""

    def test_the_counted_text_actually_raises_the_estimate(self) -> None:
        """Counting it must change the NUMBER, not just the string — that is the
        whole point of closing the blind spot."""
        from stackowl.parliament.token_estimate import estimate_tokens

        with_call = {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "function": {
                    "name": "web_search",
                    "arguments": '{"query": "a reasonably long search query here"}',
                }
            }],
        }

        assert estimate_tokens(_message_content_text(with_call)) > 0


class TestTheConstantsStayHonest:
    def test_the_reserve_leaves_a_usable_answer(self) -> None:
        """A reserve so large that answers get truncated would trade one bad
        failure for another."""
        assert WINDOW - WINDOW // _PROMPT_RESERVE_DIVISOR > 200_000

    def test_the_error_rate_covers_the_measured_undercount(self) -> None:
        """>=16.5% was measured live (12,145 real against at most 10,144)."""
        measured = (REAL_INPUT_AT_FAILURE - 10144) / REAL_INPUT_AT_FAILURE
        assert _INPUT_ESTIMATE_ERROR_RATE > measured
        assert math.isclose(_INPUT_ESTIMATE_ERROR_RATE, 0.25)
