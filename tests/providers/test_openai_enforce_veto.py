"""Behavioral test: openai ``_enforce`` closure is wired to ``decide_nudge``.

Proves that the openai provider's _enforce path — which decides whether to
inject a persistence nudge — now delegates to the shared ``decide_nudge``
helper from ``stackowl.pipeline.supervisor``, instead of using the old inline
``nudge_budget <= 0`` early-return + direct decrement pattern.

Test scenario: a lying / erroring judge (persistence_check returns None) + a
turn where a tool actually failed -> the structural veto inside ``decide_nudge``
should fire and inject the persistence directive, causing the loop to continue
rather than accept the give-up draft.

Uses the same fake-client pattern as ``test_phaseF_max_out.py``.
"""
from __future__ import annotations

from typing import Any

import pytest

from stackowl.config.provider import ProviderConfig
from stackowl.config.test_mode import TestModeGuard
from stackowl.pipeline.persistence import PERSISTENCE_DIRECTIVE, TOOL_FAILED_MARKER
from stackowl.providers.openai_provider import OpenAIProvider


# ---------------------------------------------------------------------------
# Minimal fake OpenAI client helpers (mirrors test_phaseF_max_out pattern)
# ---------------------------------------------------------------------------


class _FakeFunction:
    def __init__(self, name: str, arguments: str) -> None:
        self.name = name
        self.arguments = arguments


class _FakeToolCall:
    def __init__(self, id: str, name: str, arguments: str) -> None:
        self.id = id
        self.type = "function"
        self.function = _FakeFunction(name, arguments)


class _FakeMessage:
    def __init__(
        self,
        content: str | None,
        tool_calls: list[_FakeToolCall] | None = None,
    ) -> None:
        self.content = content
        self.tool_calls = tool_calls


class _FakeChoice:
    def __init__(self, message: _FakeMessage) -> None:
        self.message = message


class _FakeResponse:
    def __init__(self, message: _FakeMessage) -> None:
        self.choices = [_FakeChoice(message)]
        self.model = "gemma4:e4b"
        self.usage = None


class _FailThenFinalCompletions:
    """Sequence of three responses:

    1. A tool call that will be dispatched with a FAILED result (simulates a
       tool error so ``all_calls`` has a failed entry).
    2. A structurally-irrelevant give-up draft (< 4 chars) — triggers the
       ``is_structural_giveup`` veto in ``decide_nudge``.
    3. A genuine final answer after the nudge is injected — proves the loop
       continued after the veto fired.
    """

    def __init__(self) -> None:
        self._call_count = 0
        self.create_count = 0

    async def create(self, **kwargs: Any) -> _FakeResponse:
        self._call_count += 1
        self.create_count += 1
        if self._call_count == 1:
            # First call: return a native tool call.
            tc = _FakeToolCall(id="call_1", name="web_search", arguments='{"query":"test"}')
            return _FakeResponse(_FakeMessage(content=None, tool_calls=[tc]))
        if self._call_count == 2:
            # Second call: structurally-irrelevant give-up (< _MIN_RELEVANT_CHARS=4
            # chars), so is_structural_giveup fires and the veto injects the directive.
            return _FakeResponse(_FakeMessage(content="No.", tool_calls=None))
        # Third call (after nudge injection): genuine answer.
        return _FakeResponse(_FakeMessage(content="Here is the result you requested.", tool_calls=None))


class _FakeChat:
    def __init__(self, completions: _FailThenFinalCompletions) -> None:
        self.completions = completions


class _FakeClient:
    def __init__(self, completions: _FailThenFinalCompletions) -> None:
        self.chat = _FakeChat(completions)


def _make_openai_provider(client: _FakeClient) -> OpenAIProvider:
    config = ProviderConfig(
        name="ollama",
        protocol="openai",
        base_url="http://localhost:11434/v1",
        default_model="gemma4:e4b",
        tier="local",
    )
    provider = OpenAIProvider(config, api_key="")
    provider._client = client  # type: ignore[assignment]
    return provider


_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
            },
        },
    }
]


# ---------------------------------------------------------------------------
# Core behavioral test: veto fires through openai _enforce
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_openai_enforce_veto_fires_and_loop_continues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Structural veto fires via decide_nudge in the openai _enforce path.

    Setup:
    - The tool dispatcher always returns a FAILED result (TOOL_FAILED_MARKER).
    - The persistence_check (judge) returns None — simulates a lying/erroring judge.
    - The first model response is a tool call (so all_calls gains a failed entry).
    - The second model response is a give-up draft.
    - The third model response is a genuine answer.

    Expected: the veto fires on the second response (give-up + failed tool),
    nudges the loop, the loop makes a third API call, and the final returned
    text is the genuine answer — NOT the give-up draft.
    """
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    completions = _FailThenFinalCompletions()
    provider = _make_openai_provider(_FakeClient(completions))

    async def _failing_dispatcher(name: str, args: dict[str, Any]) -> str:
        # Always fail so all_calls has a failed entry for the veto to detect.
        return f"{TOOL_FAILED_MARKER}simulated failure for {name}"

    async def _lying_judge(draft: str, tool_summary: str) -> str | None:
        # Lying/silent judge — returns None even on a give-up.
        return None

    text, calls = await provider.complete_with_tools(
        user_text="please do the task",
        system_text="You are a helpful assistant.",
        tool_schemas=_SCHEMAS,
        tool_dispatcher=_failing_dispatcher,
        persistence_check=_lying_judge,
    )

    # The final answer must be the genuine answer from the 3rd call, NOT the give-up.
    assert text == "Here is the result you requested.", (
        f"Expected genuine answer after veto nudge, got: {text!r}"
    )
    # The loop made 3 API calls: tool call -> give-up (vetoed+nudged) -> genuine answer.
    assert completions.create_count == 3, (
        f"Expected 3 API calls (tool + give-up + genuine), got {completions.create_count}"
    )
    # The failed tool call must be recorded.
    assert any(c.get("failed") for c in calls), "Expected at least one failed tool call in records"


@pytest.mark.asyncio
async def test_openai_enforce_veto_budget_exhaustion_accepts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the veto fires twice (budget exhausted), the third give-up is accepted.

    This proves the budget cap in decide_nudge works through openai _enforce:
    after nudge_budget=2 nudges are spent, a further give-up is accepted (no
    infinite loop).
    """
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    class _AlwaysGiveUpCompletions:
        """First call: tool (failed). All subsequent: structurally-irrelevant give-up."""

        def __init__(self) -> None:
            self._call_count = 0
            self.create_count = 0

        async def create(self, **kwargs: Any) -> _FakeResponse:
            self._call_count += 1
            self.create_count += 1
            if self._call_count == 1:
                tc = _FakeToolCall(id="call_1", name="web_search", arguments='{"query":"test"}')
                return _FakeResponse(_FakeMessage(content=None, tool_calls=[tc]))
            # Always a trivial give-up (< _MIN_RELEVANT_CHARS=4 chars) so the
            # structural veto fires until budget exhaustion, then accepts.
            return _FakeResponse(
                _FakeMessage(content="No.", tool_calls=None)
            )

    completions = _AlwaysGiveUpCompletions()
    provider = _make_openai_provider(_FakeClient(completions))

    async def _failing_dispatcher(name: str, args: dict[str, Any]) -> str:
        return f"{TOOL_FAILED_MARKER}simulated failure"

    async def _lying_judge(draft: str, tool_summary: str) -> str | None:
        return None

    text, calls = await provider.complete_with_tools(
        user_text="do the thing",
        system_text="sys",
        tool_schemas=_SCHEMAS,
        tool_dispatcher=_failing_dispatcher,
        persistence_check=_lying_judge,
    )

    # Eventually accepted: text is the trivial give-up draft after budget runs out.
    assert text == "No.", (
        f"Expected trivial give-up text accepted after budget exhaustion, got: {text!r}"
    )
    # Loop must have terminated (not hit max_iterations).
    # With nudge_budget=2: 1 tool call + 2 nudges + 1 accepted give-up = 4 calls min.
    # (Plus 1 final accepted = total 4 API calls).
    assert completions.create_count >= 4, (
        f"Expected at least 4 calls (1 tool + 2 nudges + 1 accept), got {completions.create_count}"
    )
    # Must NOT have hit max_iterations (30). If budget exhaustion worked, it terminates early.
    assert completions.create_count <= 10, (
        f"Loop should terminate well before max_iterations, got {completions.create_count}"
    )
