"""The safety margin is a RESERVE, not a budget to hand back.

LIVE ON 2026-08-21 — ten `ContextWindowExceededError` 400s in the current log, at
`openai_provider.py:759`, i.e. the tool-loop round with `tool_schemas` already counted.
The provider's own words:

    "This model's maximum context length is 262144 tokens. However, you requested
     189999 output tokens and your prompt contains at least 72146 input tokens,
     for a total of at least 262145 tokens."

262145. **One token over.** That is not bad luck; it is the arithmetic:

    margin   = max(2000, input_est * 0.25)          # _INPUT_ESTIMATE_ERROR_RATE
    headroom = window - input_est - margin
    max_tokens = min(max_output_tokens, headroom, window - window//8)

Work it backwards from the observed request: 189999 = 262144 - 1.25 * input_est gives
input_est ≈ 57,716 against a real 72,146 — a 25.0% undercount, EXACTLY the rate the
code declares. So:

    input_est + margin      = 57,716 + 14,429 = 72,145   ≈ the real input
    + max_tokens (headroom) = 262,144 - 72,145 = 190,000  (log says 189,999)
    ------------------------------------------------------
    total                   = the window, precisely

THE MARGIN CANCELS THE ERROR AND THEN THE REMAINDER IS SPENT IN FULL, so a request
whose estimate is wrong by exactly the admitted rate lands ON the ceiling with zero
slack. One token of jitter overflows it. The margin was computed, then given away.

AND THE SELF-HEAL CANNOT CATCH IT. `_resilient_round.py:230` maps
`PAYLOAD_TOO_LARGE` from status **413** only; this gateway returns the overflow as a
**400**, which classifies as `BAD_REQUEST` -> `RecoveryAction.ABORT`. Measured: the
COMPRESS actuator has fired **0 times across all 31 retained log files** — it has never
once executed — and `note_payload_limit` therefore never learned a smaller ceiling.
That half is D04.1's thesis ("which status means too big" is a BACKEND FACT hardcoded
in shared code) and is recorded there; this file fixes the arithmetic that makes the
overflow happen at all, which is the part that needs no per-backend declaration.
"""

from __future__ import annotations

import pytest

from stackowl.config.provider import ProviderConfig
from stackowl.providers.openai_provider import OpenAIProvider

_WINDOW = 262_144


def _provider(monkeypatch, *, window: int = _WINDOW) -> OpenAIProvider:
    from stackowl.providers import openai_provider as mod

    monkeypatch.setattr(mod, "cached_window", lambda *_a, **_k: window, raising=False)
    import stackowl.providers.model_window as mw

    monkeypatch.setattr(mw, "cached_window", lambda *_a, **_k: window)
    cfg = ProviderConfig(
        name="NeraAiRaw", protocol="openai", default_model="neraai-v1-raw",
        tiers=("fast",), base_url="http://gw/v1", max_output_tokens=250_000,
    )
    return OpenAIProvider(cfg, "k")


class _Msg:
    def __init__(self, text: str) -> None:
        self.content = text
        self.role = "user"


class TestTheRequestCanNeverLandOnTheWindow:
    def test_an_estimate_wrong_by_its_own_declared_rate_still_fits(
        self, monkeypatch
    ) -> None:
        """THE LIVE CASE. If the estimate undercounts by exactly the rate the code
        declares — which is what happened — the request must still fit, with room to
        spare. Before the fix this summed to exactly the window."""
        from stackowl.providers import openai_provider as mod

        p = _provider(monkeypatch)
        est = 57_716
        monkeypatch.setattr(mod, "estimate_tokens", lambda _t: est, raising=False)
        import stackowl.parliament.token_estimate as te

        monkeypatch.setattr(te, "estimate_tokens", lambda _t: est)

        cap = p._output_cap("neraai-v1-raw", [_Msg("x")])  # noqa: SLF001

        real_input = int(est * 1.25)  # the error rate the code itself declares
        assert real_input + cap < _WINDOW, (
            f"request lands on or over the window: {real_input} + {cap} "
            f"= {real_input + cap} vs {_WINDOW}"
        )

    def test_there_is_real_slack_not_merely_one_token(self, monkeypatch) -> None:
        """"Fits" is not enough — the live failure missed by ONE token. The reserve
        has to be big enough that ordinary jitter cannot cross it."""
        from stackowl.providers import openai_provider as mod

        p = _provider(monkeypatch)
        est = 57_716
        monkeypatch.setattr(mod, "estimate_tokens", lambda _t: est, raising=False)
        import stackowl.parliament.token_estimate as te

        monkeypatch.setattr(te, "estimate_tokens", lambda _t: est)

        cap = p._output_cap("neraai-v1-raw", [_Msg("x")])  # noqa: SLF001
        slack = _WINDOW - (int(est * 1.25) + cap)

        assert slack >= int(est * 0.2), f"only {slack} tokens of slack"

    @pytest.mark.parametrize("est", [1_000, 10_000, 57_716, 120_000, 200_000])
    def test_it_holds_across_prompt_sizes(self, monkeypatch, est: int) -> None:
        """The property must not depend on where in the window the prompt sits — the
        failure was at ~57k, but a bigger prompt has a bigger absolute error."""
        from stackowl.providers import openai_provider as mod

        p = _provider(monkeypatch)
        monkeypatch.setattr(mod, "estimate_tokens", lambda _t: est, raising=False)
        import stackowl.parliament.token_estimate as te

        monkeypatch.setattr(te, "estimate_tokens", lambda _t: est)

        cap = p._output_cap("neraai-v1-raw", [_Msg("x")])  # noqa: SLF001

        assert int(est * 1.25) + cap < _WINDOW

    def test_a_small_prompt_still_gets_a_generous_budget(self, monkeypatch) -> None:
        """The reserve must not turn into a shaping cap. A short prompt should still
        be bounded by max_output_tokens or the window reserve, not by the margin."""
        from stackowl.providers import openai_provider as mod

        p = _provider(monkeypatch)
        monkeypatch.setattr(mod, "estimate_tokens", lambda _t: 500, raising=False)
        import stackowl.parliament.token_estimate as te

        monkeypatch.setattr(te, "estimate_tokens", lambda _t: 500)

        cap = p._output_cap("neraai-v1-raw", [_Msg("hi")])  # noqa: SLF001

        assert cap > 100_000, f"a 500-token prompt was capped to {cap}"
