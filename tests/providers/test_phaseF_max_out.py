"""Phase F — raise tool-iteration budget (8 -> 30) + graceful max-out.

Diagnosed from a live turn: the agent did real multi-step work, hit
``max_iterations reached`` at the old budget of 8, and the loop returned ``""``
(empty) — the user got silence. Phase F:

  F1. ``ProviderConfig().tool_max_iterations == 30`` and the loop runs ~30 tool
      iterations (not 8) before max-out when every iteration returns a tool call.

  F2. On max-out the loop makes ONE final model call WITHOUT ``tools=`` (a global,
      language-agnostic wrap-up) and returns the NON-EMPTY wrap-up text — never "".
      Fail-open: if the wrap-up call raises, it falls back to the last assistant
      text already in context (and still does not raise / does not return empty).

Covered for OpenAI end-to-end; mirrored for Anthropic (same flow / same constant).
Reuses the fake-client pattern from test_react_protocol / test_phaseE_context_budget.
"""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.config.provider import ProviderConfig
from stackowl.config.test_mode import TestModeGuard
from stackowl.providers._wrapup import WRAPUP_DIRECTIVE
from stackowl.providers.anthropic_provider import AnthropicProvider
from stackowl.providers.openai_provider import OpenAIProvider

# --------------------------------------------------------------------------- #
# F1 — config value
# --------------------------------------------------------------------------- #


def test_tool_max_iterations_default_aligns_with_step_backstop() -> None:
    # F028/REACT-2 — the provider loop ceiling default is reconciled with the
    # default per-turn step backstop (DEFAULT_TURN_MAX_STEPS) so the two bounds
    # agree by construction. On the no-explicit-caps path the governor already cut
    # at DEFAULT_TURN_MAX_STEPS, so the live default-path budget is unchanged; this
    # only removes the dead +10 headroom that let the provider ceiling drift higher
    # than the governor. (Was 30; Phase F's real fix was raising 8 -> the backstop.)
    from stackowl.authz.bounds import DEFAULT_TURN_MAX_STEPS

    config = ProviderConfig(
        name="ollama",
        protocol="openai",
        default_model="gemma4:e4b",
        tier="local",
    )
    assert config.tool_max_iterations == DEFAULT_TURN_MAX_STEPS


# --------------------------------------------------------------------------- #
# OpenAI fake client — records whether `tools` was passed on each create() call.
# --------------------------------------------------------------------------- #


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
    def __init__(self, content: str | None, tool_calls: list[_FakeToolCall] | None = None) -> None:
        self.content = content
        self.tool_calls = tool_calls


class _FakeChoice:
    def __init__(self, message: _FakeMessage) -> None:
        self.message = message


class _FakeResponse:
    def __init__(self, message: _FakeMessage) -> None:
        self.choices = [_FakeChoice(message)]
        self.model = "gemma4:e4b"


class _ToolEveryTimeCompletions:
    """Returns a tool_call on EVERY tool-bearing call so the loop never finalizes.

    On the tool-FREE call (the wrap-up, no ``tools`` kwarg) it returns a distinctive
    non-empty string so the test can prove the wrap-up text is delivered.
    """

    def __init__(
        self, *, tool_call: bool, raise_on_wrapup: bool = False, sleep_on_wrapup_s: float = 0.0,
    ) -> None:
        self._tool_call = tool_call
        self._raise_on_wrapup = raise_on_wrapup
        self._sleep_on_wrapup_s = sleep_on_wrapup_s
        self.tools_seen: list[bool] = []  # per-call: was `tools` passed?
        self.max_tokens_seen: list[int] = []  # per-call: the max_tokens value used
        self.create_count = 0

    async def create(self, **kwargs: Any) -> _FakeResponse:
        self.create_count += 1
        has_tools = "tools" in kwargs and kwargs["tools"] is not None
        self.tools_seen.append(has_tools)
        self.max_tokens_seen.append(kwargs.get("max_tokens"))  # type: ignore[arg-type]
        if not has_tools:
            # This is the wrap-up call.
            if self._sleep_on_wrapup_s:
                import asyncio
                await asyncio.sleep(self._sleep_on_wrapup_s)
            if self._raise_on_wrapup:
                raise RuntimeError("simulated provider failure on wrap-up call")
            return _FakeResponse(_FakeMessage(content="WRAPUP-ANSWER-PHASEF", tool_calls=None))
        if self._tool_call:
            # Vary args per call so the loop-guard never fires (this test exercises
            # max-iterations / Phase F, not the repeated-call guard).
            tc = _FakeToolCall(
                id=f"call_{self.create_count}",
                name="web_search",
                arguments=f'{{"query":"q{self.create_count}"}}',
            )
            return _FakeResponse(_FakeMessage(content=None, tool_calls=[tc]))
        # ReAct text action (no native tool_calls) — vary query to avoid guard.
        return _FakeResponse(
            _FakeMessage(
                content=f'ACTION: web_search\n```json\n{{"query":"q{self.create_count}"}}\n```',
                tool_calls=None,
            )
        )


class _FakeChat:
    def __init__(self, completions: _ToolEveryTimeCompletions) -> None:
        self.completions = completions


class _FakeClient:
    def __init__(self, completions: _ToolEveryTimeCompletions) -> None:
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
            "description": "Search.",
            "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
        },
    }
]


async def _dispatcher(name: str, args: dict[str, Any]) -> str:
    return "some observation"


# --------------------------------------------------------------------------- #
# F1 — loop runs ~30 iterations before max-out.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_loop_runs_about_thirty_iterations_before_maxout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    completions = _ToolEveryTimeCompletions(tool_call=True)
    provider = _make_openai_provider(_FakeClient(completions))

    text, calls = await provider.complete_with_tools(
        user_text="go",
        system_text="sys",
        tool_schemas=_SCHEMAS,
        tool_dispatcher=_dispatcher,
    )

    # tool-bearing create() calls == iterations; the extra one is the wrap-up.
    tool_iterations = sum(1 for t in completions.tools_seen if t)
    wrapup_calls = sum(1 for t in completions.tools_seen if not t)
    assert wrapup_calls == 1, "exactly one tool-free wrap-up call expected at max-out"
    # Loose/robust: clearly more than the old budget of 8, ~DEFAULT_TURN_MAX_STEPS.
    from stackowl.authz.bounds import DEFAULT_TURN_MAX_STEPS

    assert tool_iterations >= DEFAULT_TURN_MAX_STEPS - 5, (
        f"expected ~{DEFAULT_TURN_MAX_STEPS} tool iterations, got {tool_iterations}"
    )
    assert tool_iterations <= DEFAULT_TURN_MAX_STEPS + 1


# --------------------------------------------------------------------------- #
# F2 — graceful max-out: final tool-free call, non-empty wrap-up text.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_call", [True, False])
async def test_maxout_makes_toolfree_call_and_returns_nonempty(
    monkeypatch: pytest.MonkeyPatch, tool_call: bool
) -> None:
    """The KEY test: at max-out a final call is made with NO tools and the
    non-empty wrap-up text is returned — NOT "". Fails if F2 is reverted to
    ``return "", all_calls``. Covers both native tool_calls and ReAct text."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    completions = _ToolEveryTimeCompletions(tool_call=tool_call)
    provider = _make_openai_provider(_FakeClient(completions))

    text, calls = await provider.complete_with_tools(
        user_text="go",
        system_text="sys",
        tool_schemas=_SCHEMAS,
        tool_dispatcher=_dispatcher,
    )

    # Returned text is the non-empty wrap-up answer, not empty.
    assert text == "WRAPUP-ANSWER-PHASEF"
    assert text != ""

    # The final create() call carried NO tools (the wrap-up call).
    assert completions.tools_seen[-1] is False, "final wrap-up call must omit tools="
    # All prior calls carried tools.
    assert all(completions.tools_seen[:-1]), "only the last call may be tool-free"


@pytest.mark.asyncio
async def test_maxout_failopen_falls_back_to_last_assistant_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the wrap-up call raises, fail-open to the last non-empty assistant text
    already in context — do not raise, do not return empty."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    completions = _ToolEveryTimeCompletions(tool_call=True, raise_on_wrapup=True)
    provider = _make_openai_provider(_FakeClient(completions))

    # Seed a prior assistant text in history so a fallback target exists. The loop
    # appends assistant turns with tool_calls; their `content` is the seeded text.
    async def dispatcher(name: str, args: dict[str, Any]) -> str:
        return "obs"

    # Make the model emit assistant content alongside the tool call so a non-empty
    # assistant text lands in `messages` for the fallback to find.
    original_create = completions.create

    async def create_with_content(**kwargs: Any) -> _FakeResponse:
        resp = await original_create(**kwargs)
        if resp.choices[0].message.tool_calls:
            resp.choices[0].message.content = "PROGRESS-SO-FAR"
        return resp

    completions.create = create_with_content  # type: ignore[assignment]

    text, calls = await provider.complete_with_tools(
        user_text="go",
        system_text="sys",
        tool_schemas=_SCHEMAS,
        tool_dispatcher=dispatcher,
    )

    # Did not raise, did not return empty; fell back to the last assistant text.
    assert text == "PROGRESS-SO-FAR"
    assert text != ""


# --------------------------------------------------------------------------- #
# Anthropic mirror — same wrap-up flow / constant.
# --------------------------------------------------------------------------- #


class _ABlock:
    def __init__(self, type: str, text: str = "", id: str = "", name: str = "", input: Any = None) -> None:
        self.type = type
        self.text = text
        self.id = id
        self.name = name
        self.input = input or {}


class _AResponse:
    def __init__(self, stop_reason: str, content: list[_ABlock]) -> None:
        self.stop_reason = stop_reason
        self.content = content


class _AnthropicMessages:
    """Returns a tool_use stop on every tool-bearing call; non-empty text on the
    tool-free wrap-up call."""

    def __init__(self, *, sleep_on_wrapup_s: float = 0.0) -> None:
        self._sleep_on_wrapup_s = sleep_on_wrapup_s
        self.tools_seen: list[bool] = []
        self.max_tokens_seen: list[int] = []
        self.create_count = 0

    async def create(self, **kwargs: Any) -> _AResponse:
        self.create_count += 1
        has_tools = "tools" in kwargs and kwargs["tools"] is not None
        self.tools_seen.append(has_tools)
        self.max_tokens_seen.append(kwargs.get("max_tokens"))  # type: ignore[arg-type]
        if not has_tools:
            if self._sleep_on_wrapup_s:
                import asyncio
                await asyncio.sleep(self._sleep_on_wrapup_s)
            return _AResponse("end_turn", [_ABlock("text", text="WRAPUP-ANSWER-ANTHROPIC")])
        # Vary args per call so the loop-guard never fires (this test exercises
        # max-iterations / Phase F, not the repeated-call guard).
        return _AResponse(
            "tool_use",
            [_ABlock(
                "tool_use", id=f"tu_{self.create_count}", name="web_search",
                input={"query": f"q{self.create_count}"},
            )],
        )


class _AnthropicClient:
    def __init__(self, messages: _AnthropicMessages) -> None:
        self.messages = messages


def _make_anthropic_provider(client: _AnthropicClient) -> AnthropicProvider:
    config = ProviderConfig(
        name="claude",
        protocol="anthropic",
        default_model="claude-sonnet",
        tier="powerful",
    )
    provider = AnthropicProvider(config, api_key="x")
    provider._client = client  # type: ignore[assignment]
    return provider


@pytest.mark.asyncio
async def test_anthropic_maxout_makes_toolfree_call_and_returns_nonempty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    messages = _AnthropicMessages()
    provider = _make_anthropic_provider(_AnthropicClient(messages))

    text, calls = await provider.complete_with_tools(
        user_text="go",
        system_text="sys",
        tool_schemas=_SCHEMAS,
        tool_dispatcher=_dispatcher,
    )

    assert text == "WRAPUP-ANSWER-ANTHROPIC"
    assert text != ""
    assert messages.tools_seen[-1] is False, "final wrap-up call must omit tools="
    assert all(messages.tools_seen[:-1]), "only the last call may be tool-free"
    # Sanity: ran more than the old budget of 8 (~DEFAULT_TURN_MAX_STEPS now).
    from stackowl.authz.bounds import DEFAULT_TURN_MAX_STEPS

    tool_iterations = sum(1 for t in messages.tools_seen if t)
    assert tool_iterations >= DEFAULT_TURN_MAX_STEPS - 5


# --------------------------------------------------------------------------- #
# F026 — the max-out WRAP-UP final answer is vetoed when it is a dressed-up
# give-up (a consequential action failed with no success this turn). The terminal
# is structural: is_consequential_giveup_now() (pure, no LLM judge, no nudge —
# there is no loop to continue) → synthesize_from_calls replaces the prose.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_openai_wrapup_giveup_vetoed_to_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """At max-out, when a consequential action FAILED with no success, the wrap-up
    prose (a dressed-up give-up) is replaced by the honest floor naming the
    capability — NOT returned as the answer."""
    from stackowl.infra import tool_outcome_ledger as tol

    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    completions = _ToolEveryTimeCompletions(tool_call=True)
    provider = _make_openai_provider(_FakeClient(completions))

    token = tol.bind()
    try:
        # Arm the ledger: a consequential action failed this turn, none succeeded.
        tol.record_tool_outcome(
            name="send_email", action_severity="consequential", success=False,
        )
        text, calls = await provider.complete_with_tools(
            user_text="send the report email",
            system_text="sys",
            tool_schemas=_SCHEMAS,
            tool_dispatcher=_dispatcher,
        )
    finally:
        tol.reset(token)

    # The dressed-up wrap-up prose must NOT be returned — the floor replaces it.
    assert text != "WRAPUP-ANSWER-PHASEF", (
        "F026 FAIL: the dressed-up wrap-up give-up was returned as the answer."
    )
    assert text.strip(), "the floor must be non-empty"
    assert "couldn" in text.lower() or "could not" in text.lower()


@pytest.mark.asyncio
async def test_openai_wrapup_legit_answer_not_vetoed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LM-13 — with NO consequential failure armed, the legit wrap-up prose is
    returned unchanged (gate on dishonest-give-up, never on max-out reached)."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    completions = _ToolEveryTimeCompletions(tool_call=True)
    provider = _make_openai_provider(_FakeClient(completions))

    text, calls = await provider.complete_with_tools(
        user_text="research the topic",
        system_text="sys",
        tool_schemas=_SCHEMAS,
        tool_dispatcher=_dispatcher,
    )
    assert text == "WRAPUP-ANSWER-PHASEF"


@pytest.mark.asyncio
async def test_anthropic_wrapup_giveup_vetoed_to_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Anthropic mirror of F026 — dressed-up wrap-up give-up → honest floor."""
    from stackowl.infra import tool_outcome_ledger as tol

    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    messages = _AnthropicMessages()
    provider = _make_anthropic_provider(_AnthropicClient(messages))

    token = tol.bind()
    try:
        tol.record_tool_outcome(
            name="send_email", action_severity="consequential", success=False,
        )
        text, calls = await provider.complete_with_tools(
            user_text="send the report email",
            system_text="sys",
            tool_schemas=_SCHEMAS,
            tool_dispatcher=_dispatcher,
        )
    finally:
        tol.reset(token)

    assert text != "WRAPUP-ANSWER-ANTHROPIC", (
        "F026 FAIL (anthropic): the dressed-up wrap-up give-up was returned."
    )
    assert text.strip()
    assert "couldn" in text.lower() or "could not" in text.lower()


# --------------------------------------------------------------------------- #
# F027 — the terminal wrap-up create() is bounded by wrapup_deadline_s. A hung
# wrap-up must return within the deadline carrying the honest floor (non-empty),
# never hang past the promised turn bound. None → byte-identical to today.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_openai_wrapup_bounded_by_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A wrap-up create() that sleeps far past the deadline must be cut off and
    return the honest floor within ~the deadline — not hang."""
    import asyncio
    import time

    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    completions = _ToolEveryTimeCompletions(tool_call=True, sleep_on_wrapup_s=10.0)
    provider = _make_openai_provider(_FakeClient(completions))

    t0 = time.monotonic()
    text, calls = await provider.complete_with_tools(
        user_text="go",
        system_text="sys",
        tool_schemas=_SCHEMAS,
        tool_dispatcher=_dispatcher,
        wrapup_deadline_s=0.2,
    )
    elapsed = time.monotonic() - t0

    assert elapsed < 5.0, f"wrap-up was not bounded by the deadline — took {elapsed:.2f}s"
    assert text.strip(), "must return the non-empty honest floor on a timed-out wrap-up"
    assert text != "WRAPUP-ANSWER-PHASEF", "the slept wrap-up text must NOT be returned"
    # Confirm the sentinel — a deadline-cut wrap-up routes to the fail-open floor.
    assert isinstance(text, str)
    del asyncio  # silence unused-import if the path returns before awaiting it


@pytest.mark.asyncio
async def test_openai_wrapup_deadline_none_is_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """wrapup_deadline_s=None (default) → today's behavior, the wrap-up returns."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    completions = _ToolEveryTimeCompletions(tool_call=True)
    provider = _make_openai_provider(_FakeClient(completions))

    text, calls = await provider.complete_with_tools(
        user_text="go", system_text="sys", tool_schemas=_SCHEMAS,
        tool_dispatcher=_dispatcher, wrapup_deadline_s=None,
    )
    assert text == "WRAPUP-ANSWER-PHASEF"


@pytest.mark.asyncio
async def test_anthropic_wrapup_bounded_by_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Anthropic mirror — a hung wrap-up is bounded and returns the honest floor."""
    import time

    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    messages = _AnthropicMessages(sleep_on_wrapup_s=10.0)
    provider = _make_anthropic_provider(_AnthropicClient(messages))

    t0 = time.monotonic()
    text, calls = await provider.complete_with_tools(
        user_text="go", system_text="sys", tool_schemas=_SCHEMAS,
        tool_dispatcher=_dispatcher, wrapup_deadline_s=0.2,
    )
    elapsed = time.monotonic() - t0

    assert elapsed < 5.0, f"anthropic wrap-up not bounded — took {elapsed:.2f}s"
    assert text.strip()
    assert text != "WRAPUP-ANSWER-ANTHROPIC"


def test_wrapup_directive_is_nonempty_and_global() -> None:
    # The directive carries no case specifics and instructs a tool-free answer.
    assert WRAPUP_DIRECTIVE
    assert "Do not call any tool" in WRAPUP_DIRECTIVE


# --------------------------------------------------------------------------- #
# max_tokens override (live incident 2026-07-22) — an explicit caller-supplied
# max_tokens must reach every create() call, both in-loop tool rounds and the
# terminal wrap-up; omitting it preserves today's _output_cap/config default.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_openai_complete_with_tools_honors_explicit_max_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    completions = _ToolEveryTimeCompletions(tool_call=True)
    provider = _make_openai_provider(_FakeClient(completions))

    await provider.complete_with_tools(
        user_text="go", system_text="sys", tool_schemas=_SCHEMAS,
        tool_dispatcher=_dispatcher, max_tokens=512,
    )

    assert all(m == 512 for m in completions.max_tokens_seen)


@pytest.mark.asyncio
async def test_openai_complete_with_tools_max_tokens_none_is_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    completions = _ToolEveryTimeCompletions(tool_call=True)
    provider = _make_openai_provider(_FakeClient(completions))

    await provider.complete_with_tools(
        user_text="go", system_text="sys", tool_schemas=_SCHEMAS,
        tool_dispatcher=_dispatcher,
    )

    # Byte-identical to today: no explicit override, none of the calls used 512.
    assert 512 not in completions.max_tokens_seen


@pytest.mark.asyncio
async def test_anthropic_complete_with_tools_honors_explicit_max_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    messages = _AnthropicMessages()
    provider = _make_anthropic_provider(_AnthropicClient(messages))

    await provider.complete_with_tools(
        user_text="go", system_text="sys", tool_schemas=_SCHEMAS,
        tool_dispatcher=_dispatcher, max_tokens=512,
    )

    assert all(m == 512 for m in messages.max_tokens_seen)
