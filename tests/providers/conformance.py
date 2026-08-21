"""One scripted transcript, rendered into two wire dialects, driven through both
``complete_with_tools`` implementations.

WHY THIS EXISTS. `openai_provider.complete_with_tools` and
`anthropic_provider.complete_with_tools` are ~647 and ~548 lines implementing the same
platform spine — message assembly, LoopGuard, parse_react_action, looks_like_tool_call,
tool dispatch, persistence_check, checkpointing, resume, decide_nudge, the ESCALATE
sentinel, the wrap-up deadline, the floor. They call the same shared helpers in the same
order at parallel offsets. Only the `create()` call and the block shapes are真 dialect.

D04.1's brainstorm proposed extracting that spine. The Stability lens objected that
there is no behaviour-preserving extraction available, because the two bodies have
DIVERGED — and that an extraction silently picks a winner, with the losing side's
behaviour vanishing under a warning that merely stops appearing. Nothing watches for
absences, and coverage is asymmetric (51 test files reference the OpenAI provider, 16
the Anthropic one), so the shared loop would be written against 3.2x more evidence from
one side.

So this converts "they have diverged" from an assertion into an executable inventory.
It is diagnostic, not a change: it is worth having even if the extraction is cancelled,
because it is what makes the extraction survivable if it is not — and because ESC-21
cannot be answered well without knowing exactly what differs.

WHAT IT DELIBERATELY DOES NOT DO. It does not assert that the two agree. Several
scenarios here are EXPECTED to differ today, and each is marked with the divergence it
demonstrates. Asserting agreement would turn a measurement into a failing build; the
point is the inventory, so the differences are RECORDED as expectations and a change in
either direction fails loudly.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from stackowl.config.provider import ProviderConfig
from stackowl.providers.anthropic_provider import AnthropicProvider
from stackowl.providers.openai_provider import OpenAIProvider

# --------------------------------------------------------------------------- #
# The dialect-agnostic script
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Say:
    """The model returns final text."""

    text: str


@dataclass(frozen=True)
class Call:
    """The model emits ONE native tool call."""

    name: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ReActCall:
    """The model emits a tool call as TEXT, in the platform's own ``ACTION:`` protocol.

    A DIFFERENT CODE PATH from `Call`, and the one that matters here: the native path
    is short, but the text-ReAct path is where the two loops order their steering-fold
    callback and their `parse_react_action` differently. A scripted transcript that
    only ever emits native calls cannot see that, which is why an early version of
    this harness reported the callback counts as identical.
    """

    name: str
    args: dict[str, Any] = field(default_factory=dict)

    def as_text(self) -> str:
        return f"ACTION: {self.name}\n```json\n{json.dumps(self.args)}\n```"


Turn = Say | Call | ReActCall


# --------------------------------------------------------------------------- #
# OpenAI dialect
# --------------------------------------------------------------------------- #


class _Fn:
    def __init__(self, name: str, arguments: str) -> None:
        self.name, self.arguments = name, arguments


class _ToolCall:
    def __init__(self, id: str, name: str, arguments: str) -> None:
        self.id, self.type, self.function = id, "function", _Fn(name, arguments)


class _Msg:
    def __init__(self, content: str | None, tool_calls: list[_ToolCall] | None = None) -> None:
        self.content, self.tool_calls = content, tool_calls


class _Choice:
    def __init__(self, msg: _Msg) -> None:
        self.message = msg


class _Resp:
    def __init__(self, msg: _Msg) -> None:
        self.choices, self.model, self.usage = [_Choice(msg)], "scripted", None


class _OpenAICompletions:
    def __init__(self, script: list[Turn], loop_last: bool) -> None:
        self._script, self._loop_last, self.n = script, loop_last, 0

    async def create(self, **_kw: Any) -> _Resp:
        turn = _next_turn(self._script, self.n, self._loop_last)
        self.n += 1
        if isinstance(turn, ReActCall):
            return _Resp(_Msg(content=turn.as_text()))
        if isinstance(turn, Say):
            return _Resp(_Msg(content=turn.text))
        return _Resp(_Msg(
            content=None,
            tool_calls=[_ToolCall(f"c{self.n}", turn.name, json.dumps(turn.args))],
        ))


class _OpenAIClient:
    def __init__(self, completions: _OpenAICompletions) -> None:
        class _Chat:
            def __init__(self, c: _OpenAICompletions) -> None:
                self.completions = c

        self.chat = _Chat(completions)
        self.completions = completions


# --------------------------------------------------------------------------- #
# Anthropic dialect
# --------------------------------------------------------------------------- #


class _ABlock:
    def __init__(self, type: str, text: str = "", **kw: Any) -> None:
        self.type, self.text = type, text
        for k, v in kw.items():
            setattr(self, k, v)


class _AResponse:
    def __init__(self, stop_reason: str, content: list[_ABlock]) -> None:
        self.stop_reason, self.content, self.usage = stop_reason, content, None


class _AnthropicMessages:
    def __init__(self, script: list[Turn], loop_last: bool) -> None:
        self._script, self._loop_last, self.n = script, loop_last, 0

    async def count_tokens(self, **_kw: Any) -> Any:
        """The REAL Anthropic client has this, and `_measure_spans` calls it for the
        D01.2 cache-breakpoint measurement. Omitting it made every anthropic run raise
        an AttributeError into a fail-open handler — harmless, but a double that is
        missing a method the real one has is exactly how a test stops resembling
        production."""

        class _Counted:
            input_tokens = 100

        return _Counted()

    async def create(self, **_kw: Any) -> _AResponse:
        turn = _next_turn(self._script, self.n, self._loop_last)
        self.n += 1
        if isinstance(turn, ReActCall):
            return _AResponse("end_turn", [_ABlock("text", text=turn.as_text())])
        if isinstance(turn, Say):
            return _AResponse("end_turn", [_ABlock("text", text=turn.text)])
        return _AResponse("tool_use", [_ABlock(
            "tool_use", id=f"c{self.n}", name=turn.name, input=dict(turn.args),
        )])


class _AnthropicClient:
    def __init__(self, messages: _AnthropicMessages) -> None:
        self.messages = messages


def _next_turn(script: list[Turn], n: int, loop_last: bool) -> Turn:
    if n < len(script):
        return script[n]
    if loop_last and script:
        last = script[-1]
        # A repeated tool call must stay DISTINCT or a dedupe guard would swallow it,
        # which would silence the very spiral the scenario is reproducing.
        if isinstance(last, Call):
            return Call(last.name, {**last.args, "_n": n})
        if isinstance(last, ReActCall):
            return ReActCall(last.name, {**last.args, "_n": n})
        return last
    return Say("fallback")


# --------------------------------------------------------------------------- #
# Driving both
# --------------------------------------------------------------------------- #


@dataclass
class Outcome:
    """Everything an extraction must preserve, observed from OUTSIDE the loop."""

    text: str
    tool_calls: int
    callbacks: int
    wire_calls: int
    events: list[str]

    def comparable(self) -> dict[str, Any]:
        """The subset compared across dialects. `wire_calls` is excluded: the two
        protocols legitimately need different round-trip counts for one transcript."""
        return {"text": self.text, "tool_calls": self.tool_calls,
                "callbacks": self.callbacks, "events": self.events}


async def drive(
    dialect: str,
    script: list[Turn],
    *,
    loop_last: bool = False,
    can_escalate: bool = False,
    max_iterations: int = 4,
    persistence_check: Any = None,
    **kwargs: Any,
) -> Outcome:
    """Run one script through one provider and return what an outside caller can see."""
    events: list[str] = []
    callbacks = 0

    async def _dispatcher(name: str, args: dict[str, Any]) -> str:
        events.append(f"dispatch:{name}")
        return "observation"

    async def _on_iteration(*_a: Any, **_k: Any) -> None:
        nonlocal callbacks
        callbacks += 1
        events.append("callback")

    if dialect == "openai":
        completions = _OpenAICompletions(script, loop_last)
        provider: Any = OpenAIProvider(
            ProviderConfig(name="p", protocol="openai", default_model="m",
                           tiers=("fast",), base_url="http://gw/v1"),
            "k",
        )
        provider._client = _OpenAIClient(completions)  # noqa: SLF001
        schemas = [{"type": "function",
                    "function": {"name": "web_search", "description": "d",
                                 "parameters": {"type": "object"}}}]
        wire = completions
    else:
        messages = _AnthropicMessages(script, loop_last)
        provider = AnthropicProvider(
            ProviderConfig(name="p", protocol="anthropic",
                           default_model="claude-sonnet", tiers=("powerful",)),
            "k",
        )
        provider._client = _AnthropicClient(messages)  # noqa: SLF001
        schemas = [{"name": "web_search", "description": "d",
                    "input_schema": {"type": "object"}}]
        wire = messages

    text, calls = await provider.complete_with_tools(
        user_text="do the thing", system_text="sys", tool_schemas=schemas,
        tool_dispatcher=_dispatcher, max_iterations=max_iterations,
        on_iteration_complete=_on_iteration, can_escalate=can_escalate,
        persistence_check=persistence_check, **kwargs,
    )
    return Outcome(text=text, tool_calls=len(calls), callbacks=callbacks,
                   wire_calls=wire.n, events=events)


DIALECTS = ("openai", "anthropic")
