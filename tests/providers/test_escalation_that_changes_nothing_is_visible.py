"""An escalation that resolves to the same model must say so, at INFO.

MEASURED 2026-08-21 across the 31 retained log files. The ladder is fully wired — 25
`ESCALATE_SENTINEL` returns from the providers, 25 executions of the handler at
`llm_gateway.py:375`, matched. But every one of the 25 is `standard -> powerful`, and
this deployment runs ONE enabled provider (`NeraAiRaw`, `tiers: [fast, standard,
powerful]`, one `default_model`, no per-model overrides), so `resolve_tier_with_fallback`
returns the SAME `(provider, model)` for every rung.

So the escalation discards a finished attempt — 14, 15, 16 and 19 tool calls in four of
those turns — and re-runs the identical ReAct loop against the identical model. And
`on_escalate` resets the tool-outcome ledger, so the second attempt is BLIND to what the
first learned, which inverts the loop-core rule that a failure must come back "with what
failed". Three fired on 2026-08-21.

Nothing noticed, because the log line says "step up" without ever comparing the target it
steps up TO. That is the guide-star question — "if this degrades silently, what notices?"
— answered with "nothing", on a self-heal that costs a full turn every time it fires.

THIS COMMIT CHANGES NO BEHAVIOUR. Whether a no-op escalation should deliver the floor
from the attempt already made, or re-run anyway, changes what the user receives and is
ESC-22, open with Bakir. A test below pins that the re-run still happens, so the question
cannot be closed by accident. What ships here is the evidence needed to answer it.
"""

from __future__ import annotations

import logging

import pytest

from stackowl.providers.llm_gateway import escalation_target_is_identical


class _Provider:
    def __init__(self, name: str) -> None:
        self.name = name


class TestItRecognisesAnEscalationThatCannotHelp:
    def test_same_provider_and_model_is_identical(self) -> None:
        """THE LIVE CASE: one backend spanning every tier."""
        p = _Provider("NeraAiRaw")

        assert escalation_target_is_identical(p, "neraai-v1-raw", p, "neraai-v1-raw")

    def test_a_different_model_on_one_backend_is_NOT_identical(self) -> None:
        """A single provider with per-model tier overrides is a real ladder. This is
        the case the operator fixes ESC-22 with, so it must not be flagged."""
        p = _Provider("ollama")

        assert not escalation_target_is_identical(p, "qwen3.5:2b", p, "qwen3.5:122b")

    def test_a_different_backend_is_NOT_identical(self) -> None:
        assert not escalation_target_is_identical(
            _Provider("ollama"), "m", _Provider("NeraAiRaw"), "m"
        )

    def test_two_instances_of_one_backend_compare_by_NAME(self) -> None:
        """The registry may hand back distinct objects across calls; identity of the
        TARGET is what matters, not of the Python object."""
        assert escalation_target_is_identical(
            _Provider("NeraAiRaw"), "m", _Provider("NeraAiRaw"), "m"
        )

    def test_a_missing_next_provider_is_not_claimed_identical(self) -> None:
        """Fail open. An unresolvable next tier is a different problem and must not be
        reported as a no-op escalation."""
        assert not escalation_target_is_identical(_Provider("a"), "m", None, "m")


@pytest.mark.asyncio
class TestTheWasteIsAnnouncedAtINFO:
    async def test_a_no_op_escalation_logs_at_INFO_naming_what_was_discarded(
        self, caplog
    ) -> None:
        """Production runs at INFO, so this must be visible there or it is not
        evidence — the same reason D08.1's fourth acceptance check sat open for days
        behind a DEBUG line."""
        caplog.set_level(logging.INFO)
        gateway, calls = _one_provider_gateway(escalates_on_attempt=1)

        await gateway.complete_with_tools(
            user_text="hi", system_text=None, tool_schemas=[],
            tool_dispatcher=None, floor="fast", ceiling="powerful",
        )

        hits = [r for r in caplog.records
                if "running as if at the ceiling" in r.message]
        assert hits, "the ladder was a no-op and production logged nothing"
        fields = getattr(hits[0], "_fields", {})
        assert fields.get("model") == "only-model"
        assert fields.get("tiers_above"), "the line does not say what was skipped"

    async def test_a_REAL_escalation_is_not_flagged(self, caplog) -> None:
        """The line must stay rare enough to mean something."""
        caplog.set_level(logging.INFO)
        gateway, _calls = _two_provider_gateway()

        await gateway.complete_with_tools(
            user_text="hi", system_text=None, tool_schemas=[],
            tool_dispatcher=None, floor="fast", ceiling="powerful",
        )

        assert not [r for r in caplog.records if "running as if at the ceiling" in r.message]

    async def test_the_identical_re_run_IS_SKIPPED(self, caplog) -> None:
        """ESC-22, ANSWERED 2026-08-21: deliver the floor already earned.

        Was `test_the_re_run_STILL_HAPPENS` while the question was open — it pinned
        that observing the waste had not silently also changed what the user gets.
        Bakir chose to stop burning the second identical turn, so the pin inverts: the
        attempt must NOT be re-run when the target cannot differ."""
        caplog.set_level(logging.INFO)
        gateway, calls = _one_provider_gateway(escalates_on_attempt=1)

        await gateway.complete_with_tools(
            user_text="hi", system_text=None, tool_schemas=[],
            tool_dispatcher=None, floor="fast", ceiling="powerful",
        )

        assert len(calls) == 1, (
            f"the loop re-ran an identical target {len(calls)} times"
        )

    async def test_a_REAL_escalation_still_re_runs(self, caplog) -> None:
        """The half that must NOT change. A genuine ladder still discards and steps
        up; only the no-op case is short-circuited."""
        caplog.set_level(logging.INFO)
        gateway, calls = _two_provider_gateway()

        await gateway.complete_with_tools(
            user_text="hi", system_text=None, tool_schemas=[],
            tool_dispatcher=None, floor="fast", ceiling="powerful",
        )

        assert len(calls) >= 2, "a real escalation stopped stepping up"

    async def test_the_users_answer_is_the_attempts_own_text(self) -> None:
        """Skipping the re-run must not hand the user the ESCALATE sentinel — that is
        a raw control token, and it is what the discarded attempt literally returned."""
        from stackowl.providers.llm_gateway import ESCALATE_SENTINEL

        gateway, _calls = _one_provider_gateway(escalates_on_attempt=1)

        text, calls = await gateway.complete_with_tools(
            user_text="hi", system_text=None, tool_schemas=[],
            tool_dispatcher=None, floor="fast", ceiling="powerful",
        )

        assert text != ESCALATE_SENTINEL, "the sentinel reached the caller"
        assert calls, "the attempt's tool calls were discarded along with the re-run"


# --------------------------------------------------------------------------- #
# Fixtures build the gateway against the REAL registry resolution path, because
# the defect being observed IS a resolution outcome.
# --------------------------------------------------------------------------- #

def _one_provider_gateway(*, escalates_on_attempt: int):
    from stackowl.providers.llm_gateway import ESCALATE_SENTINEL

    calls: list[str] = []

    class _P:
        name = "OnlyBackend"
        supports_tools = True

        async def complete_with_tools(self, **kw):
            calls.append(kw.get("model", ""))
            # A REAL provider only emits the sentinel when it is TOLD it may. The
            # first draft of this double escalated unconditionally, which made the
            # fix look untestable — it hid the very signal the fix works through.
            if kw.get("can_escalate") and len(calls) == escalates_on_attempt:
                return ESCALATE_SENTINEL, [{"tool": "a"}, {"tool": "b"}]
            return "floor answer", [{"tool": "a"}, {"tool": "b"}]

    return _gateway({"fast": (_P(), "only-model"), "standard": (_P(), "only-model"),
                     "powerful": (_P(), "only-model")}), calls


def _two_provider_gateway():
    from stackowl.providers.llm_gateway import ESCALATE_SENTINEL

    calls: list[str] = []

    def _mk(name: str):
        class _P:
            supports_tools = True

            def __init__(self) -> None:
                self.name = name

            async def complete_with_tools(self, **kw):
                calls.append(kw.get("model", ""))
                if kw.get("can_escalate") and len(calls) == 1:
                    return ESCALATE_SENTINEL, []
                return "done", []

        return _P()

    # Every tier a DIFFERENT target. A first draft mapped fast and standard to the
    # same (weak, small) pair, so the "real escalation" fixture was itself a no-op
    # escalation and the test failed correctly — the double did not resemble the
    # scenario it was named for.
    return _gateway({"fast": (_mk("weak"), "small"), "standard": (_mk("mid"), "medium"),
                     "powerful": (_mk("strong"), "big")}), calls


def _gateway(by_tier: dict[str, tuple[object, str]]):
    from stackowl.providers.llm_gateway import LLMGateway

    class _Registry:
        def resolve_tier_with_fallback(self, tier: str):
            provider, model = by_tier[tier]
            return provider, model, None

    return LLMGateway(_Registry())  # type: ignore[arg-type]
