"""A turn that runs out of steps must deliver what it LEARNED, not just an apology.

MEASURED, trace ``f33c9fa0``. Bakir asked "What agents i have" (18 characters). The
turn ran 16 rounds, billed **683,728 input tokens**, hit the 20-step ceiling, and
delivered exactly this and nothing else::

    [stopped: I ran out of steps for this turn before I could finish.
     Ask me to continue and I'll pick up from here.]

The tool results were not lost — they were RIGHT THERE. ``BudgetBreach`` carries
``tool_call_records`` (name / args / result / failed), execute.py converts every one
of them into a typed ``ToolCall``, stamps them onto ``state.tool_calls`` … and then
calls ``synthesize_floor(attempts=[], partial=None)``. Twenty tool results are
carried past the synthesizer and thrown away at the last inch.

WHY NOT JUST PASS THEM TO ``synthesize_floor``. Because ``attempts`` renders as a
comma-joined list of tool NAMES ("I tried browser_navigate, web_fetch"). That is more
honest than silence and still does not answer the question. The findings are in
``result``, and only a model can turn them into an answer.

WHY A TOOLLESS CALL, AND WHY BUILT FROM RESULTS RATHER THAN THE TRANSCRIPT.
The reference platform re-sends the whole transcript with tools stripped. Building
from ``tool_call_records`` instead is strictly cheaper and better here:

  * no tool schemas (19,423 tokens/round in the measured trace) — the call is toolless;
  * no reasoning text, which is Bakir's own instruction ("reasoning text should not be
    in memory, only the result of the reason");
  * no wire-format conversion, so it cannot drift between the anthropic and openai
    message shapes.

The turn pays ONE extra bounded call to convert "delivered nothing" into "delivered
what it found".
"""

from __future__ import annotations

import pytest

from stackowl.pipeline.budget.salvage import (
    build_salvage_messages,
    summarize_findings,
)
from stackowl.pipeline.state import ToolCall


def _tc(name: str, result: str, *, error: str | None = None) -> ToolCall:
    return ToolCall(
        tool_name=name, args={"q": "x"}, result=result, error=error, duration_ms=1.0,
    )


# --------------------------------------------------------------------------
# build_salvage_messages — pure
# --------------------------------------------------------------------------

def test_the_findings_reach_the_prompt() -> None:
    """The whole point. A result that answers the question must be IN the call."""
    msgs = build_salvage_messages(
        "What agents do I have",
        [_tc("owl_list", "brain, mailbutler, rca_gatherer")],
    )
    body = "\n".join(m.content for m in msgs)
    assert "brain, mailbutler, rca_gatherer" in body
    assert "What agents do I have" in body


def test_it_is_TOOLLESS_by_construction() -> None:
    """No schema may ride along — that is 45% of the cost this stage exists to avoid.

    ``build_salvage_messages`` returns plain Messages; there is no tools argument to
    pass and no place to put one. This test pins the contract so a later refactor
    cannot quietly re-add tools to the salvage call.
    """
    msgs = build_salvage_messages("g", [_tc("t", "r")])
    assert all(m.role in ("system", "user") for m in msgs)
    assert all(isinstance(m.content, str) and m.content for m in msgs)


def test_a_FAILED_result_is_marked_not_presented_as_a_finding() -> None:
    """Trace 0e568f1a delivered a failure's text as though it were the answer.

    The same trap applies here: 20 browser errors must not read as 20 findings.
    """
    msgs = build_salvage_messages(
        "why is the host down",
        [
            _tc("ping", "host answered in 4ms"),
            _tc("browser_navigate", "TimeoutError: 30s", error="TimeoutError: 30s"),
        ],
    )
    body = "\n".join(m.content for m in msgs)
    assert "host answered in 4ms" in body, "the real finding must survive"
    assert "TimeoutError" not in body, (
        f"an error message was presented as a finding:\n{body}"
    )
    assert "failed" in body.lower(), (
        f"the failure was dropped silently — the model cannot tell the evidence is "
        f"partial:\n{body}"
    )


def test_identical_results_are_not_repeated() -> None:
    """A stuck loop repeats one call. Sending it 12 times pays 12x for one fact."""
    msgs = build_salvage_messages(
        "g", [_tc("search", "the same page") for _ in range(12)],
    )
    body = "\n".join(m.content for m in msgs)
    assert body.count("the same page") == 1, (
        f"a repeated result was sent {body.count('the same page')} times"
    )


def test_it_is_BOUNDED_no_matter_how_large_the_results_are() -> None:
    """A browser dump is megabytes. The salvage call must not become the new runaway."""
    huge = [_tc(f"t{i}", "x" * 200_000) for i in range(20)]
    msgs = build_salvage_messages("g", huge)
    body = "\n".join(m.content for m in msgs)
    assert len(body) < 60_000, f"salvage prompt is unbounded: {len(body)} chars"


def test_truncation_is_ANNOUNCED_never_silent() -> None:
    """A silently-trimmed answer is a confident wrong answer."""
    huge = [_tc(f"t{i}", "x" * 200_000) for i in range(20)]
    body = "\n".join(m.content for m in build_salvage_messages("g", huge))
    assert "omitted" in body.lower() or "truncat" in body.lower()


def test_no_findings_at_all_yields_NO_call() -> None:
    """Nothing to summarise means the extra call is pure waste — do not make it."""
    assert build_salvage_messages("g", []) == []
    assert build_salvage_messages("g", [_tc("t", "", error="boom")]) == []


def test_the_prompt_demands_a_SHORT_concrete_answer() -> None:
    """Bakir, 2026-08-29: "do not generate long answers burning tokens, be concrete
    and short". The salvage call is the one place we control the output shape."""
    body = "\n".join(m.content for m in build_salvage_messages("g", [_tc("t", "r")]))
    low = body.lower()
    assert "brief" in low or "short" in low or "concise" in low


# --------------------------------------------------------------------------
# summarize_findings — one bounded call, never raises
# --------------------------------------------------------------------------

class _Provider:
    def __init__(self, content: str = "You have 3 owls.", boom: bool = False) -> None:
        self.content = content
        self.boom = boom
        self.calls: list[object] = []

    async def complete(self, messages, model="", **kwargs):  # noqa: ANN001,ANN003
        self.calls.append(messages)
        if self.boom:
            raise RuntimeError("provider down")

        class _R:
            content = self.content
        return _R()


@pytest.mark.asyncio
async def test_it_returns_the_models_summary() -> None:
    p = _Provider("You have 3 owls: brain, mailbutler, rca_gatherer.")
    got = await summarize_findings(p, "m", "what agents", [_tc("owl_list", "3 owls")])
    assert got == "You have 3 owls: brain, mailbutler, rca_gatherer."
    assert len(p.calls) == 1


@pytest.mark.asyncio
async def test_a_provider_failure_degrades_to_None_and_NEVER_raises() -> None:
    """This runs on the way out of an already-failed turn.

    Raising here would replace a partial answer with a crash — strictly worse than
    the silence it exists to fix. It must fall back to the existing floor.
    """
    p = _Provider(boom=True)
    assert await summarize_findings(p, "m", "g", [_tc("t", "r")]) is None


@pytest.mark.asyncio
async def test_no_findings_makes_NO_provider_call() -> None:
    p = _Provider()
    assert await summarize_findings(p, "m", "g", []) is None
    assert p.calls == []


@pytest.mark.asyncio
async def test_an_empty_model_reply_is_None_not_an_empty_answer() -> None:
    """An empty string here would satisfy the never-empty floor while saying nothing."""
    p = _Provider("   ")
    assert await summarize_findings(p, "m", "g", [_tc("t", "r")]) is None
