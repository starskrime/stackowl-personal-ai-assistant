"""Empty reasoning-model output robustness for ``OpenAIProvider.complete()``.

Live break (2026-06-23): the local qwen3.5 reasoning model spent its whole 4096
output-token budget inside an un-stripped ``<think>`` block, so ``complete()``
returned EMPTY content. That empty string crashed the fact extractor
(``FactExtractionParseError``) and fooled the persistence judge into "failing
open" — shipping an unvetted draft.

These tests drive the guarantees on the plain ``complete()`` path:
  1. ``<think>…</think>`` reasoning blocks are stripped from the answer (thinking
     stays ON for quality; only the trace is discarded).
  2. Empty-after-strip triggers ONE retry as a cheap backstop.
  3. The artificial fixed 4096 output cap is gone — ``max_tokens`` is window-sized.
"""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.config.provider import ModelOverride, ProviderConfig
from stackowl.config.test_mode import TestModeGuard
from stackowl.providers.base import Message
from stackowl.providers.model_config import resolve_model_override
from stackowl.providers.openai_provider import OpenAIProvider, _ThinkStreamFilter, strip_think


class _FakeMessage:
    def __init__(self, content: str | None) -> None:
        self.content = content
        self.tool_calls = None


class _FakeChoice:
    def __init__(self, content: str | None) -> None:
        self.message = _FakeMessage(content)
        self.finish_reason = "length"


class _FakeResponse:
    def __init__(self, content: str | None) -> None:
        self.choices = [_FakeChoice(content)]
        self.model = "qwen3.5:2b"
        self.usage = None


class _ScriptedCompletions:
    """Returns queued contents in order, recording each call's kwargs."""

    def __init__(self, contents: list[str | None]) -> None:
        self._contents = contents
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> _FakeResponse:
        self.calls.append(kwargs)
        idx = min(len(self.calls) - 1, len(self._contents) - 1)
        return _FakeResponse(self._contents[idx])


class _FakeChat:
    def __init__(self, completions: _ScriptedCompletions) -> None:
        self.completions = completions


class _FakeClient:
    def __init__(self, completions: _ScriptedCompletions) -> None:
        self.chat = _FakeChat(completions)


def _make_provider(client: _FakeClient) -> OpenAIProvider:
    config = ProviderConfig(
        name="ollama",
        protocol="openai",
        base_url="http://localhost:11434/v1",
        default_model="qwen3.5:2b",
        tier="fast",
    )
    provider = OpenAIProvider(config, api_key="")
    provider._client = client  # type: ignore[assignment]
    return provider


def test_strip_think_removes_closed_block() -> None:
    assert strip_think("<think>reasoning here</think>\nThe answer") == "The answer"


def test_strip_think_drops_unclosed_truncated_block() -> None:
    # Truncated mid-thinking (no closing tag) ⇒ everything from <think> is reasoning.
    assert strip_think("prefix<think>still reasoning when the cap hit") == "prefix"
    assert strip_think("<think>only thinking, cut off") == ""


def test_strip_think_removes_gemini_style_thought_block() -> None:
    # Live incident 2026-07-16: NeraAiRaw (Gemini-family) emits <thought> (not
    # <think>) — an unrecognized tag shipped a raw persona-planning monologue.
    assert strip_think("<thought>planning the reply</thought>\nThe answer") == "The answer"


def test_strip_think_drops_unclosed_truncated_thought_block() -> None:
    assert strip_think("prefix<thought>still reasoning when the cap hit") == "prefix"


@pytest.mark.asyncio
async def test_complete_strips_think_block(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    completions = _ScriptedCompletions(["<think>deliberating</think>\nfinal answer"])
    provider = _make_provider(_FakeClient(completions))

    result = await provider.complete([Message(role="user", content="hi")], model="")

    assert result.content == "final answer"
    assert len(completions.calls) == 1  # no retry needed


@pytest.mark.asyncio
async def test_complete_retries_once_on_empty_after_strip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    # 1st call: all thinking, truncated → empty after strip. 2nd: real JSON.
    completions = _ScriptedCompletions(
        ["<think>thinking forever, cut off at the cap", '[{"fact":"x"}]']
    )
    provider = _make_provider(_FakeClient(completions))

    result = await provider.complete([Message(role="user", content="hi")], model="")

    assert result.content == '[{"fact":"x"}]'
    assert len(completions.calls) == 2  # retried exactly once on the empty result


@pytest.mark.asyncio
async def test_empty_retry_varies_the_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F-22 — a deterministic empty generation must not be replayed identically.

    The empty-retry VARIES the request (a brief continuation nudge appended to
    the prompt) so the retry has a chance to differ instead of reproducing the
    same empty draft.
    """
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    # 1st: all thinking, truncated → empty after strip. 2nd: a real answer.
    completions = _ScriptedCompletions(["<think>thinking, cut off", "recovered answer"])
    provider = _make_provider(_FakeClient(completions))

    result = await provider.complete([Message(role="user", content="hi")], model="")

    assert result.content == "recovered answer"
    assert len(completions.calls) == 2  # retried exactly once
    # The retry prompt is VARIED — it is not byte-identical to the first round.
    assert completions.calls[1]["messages"] != completions.calls[0]["messages"]
    assert len(completions.calls[1]["messages"]) > len(completions.calls[0]["messages"])


@pytest.mark.asyncio
async def test_empty_after_varied_retry_returns_empty_not_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F-22 — still empty after the varied retry ⇒ surface "" for the downstream
    give-up floor (honest), never raise and never pass it off as an answer."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    # Both rounds empty-after-strip (unclosed think blocks).
    completions = _ScriptedCompletions(["<think>cut off", "<think>still empty"])
    provider = _make_provider(_FakeClient(completions))

    result = await provider.complete([Message(role="user", content="hi")], model="")

    assert result.content == ""  # honest empty, handled by the downstream floor
    assert len(completions.calls) == 2  # exactly one varied retry, no loop


class _FakeDelta:
    def __init__(self, content: str | None) -> None:
        self.content = content


class _FakeStreamChoice:
    def __init__(self, content: str | None) -> None:
        self.delta = _FakeDelta(content)


class _FakeStreamChunk:
    def __init__(self, content: str | None) -> None:
        self.choices = [_FakeStreamChoice(content)]
        self.usage = None
        self.model = "neraai-v1-raw"


class _FakeStreamResponse:
    """Async-iterable of scripted deltas, mimicking the SDK's streamed response."""

    def __init__(self, deltas: list[str]) -> None:
        self._deltas = deltas

    def __aiter__(self):  # noqa: ANN204
        return self._gen()

    async def _gen(self):  # noqa: ANN202
        for d in self._deltas:
            yield _FakeStreamChunk(d)


class _ScriptedStreamCompletions:
    def __init__(self, deltas: list[str]) -> None:
        self._deltas = deltas
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> _FakeStreamResponse:
        self.calls.append(kwargs)
        return _FakeStreamResponse(self._deltas)


@pytest.mark.asyncio
async def test_stream_passes_whitespace_deltas_through_verbatim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Round-trip incident (2026-07-16): a whitespace-collapse workaround was
    added for a gateway framing bug (junk "\\n\\n\\n" between real tokens),
    then the gateway operator fixed that bug the same night. Collapsing every
    whitespace-only delta to a space THEN destroyed legitimate newlines (list
    items, paragraph breaks) the fixed gateway correctly streams as their own
    delta — flattening every multi-line reply onto one line. stream() must
    pass whitespace-only deltas through untouched, same as any other content."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    deltas = ["- item one", "\n", "- item two", "\n", "- item three"]
    completions = _ScriptedStreamCompletions(deltas)
    provider = _make_provider(_FakeClient(completions))  # type: ignore[arg-type]

    collected = [
        c async for c in provider.stream([Message(role="user", content="hi")], model="")
    ]

    assert "".join(collected) == "- item one\n- item two\n- item three"


@pytest.mark.asyncio
async def test_stream_yields_during_suppressed_thinking_not_just_silence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Root cause (2026-07-16): OwlResourceGuard's per-chunk timeout resets its
    clock on every item this generator yields. Skipping the yield entirely while
    think_filter suppresses a long <thought> block meant a genuinely active,
    slow-reasoning model got killed at the timeout ceiling and misread as hung.
    stream() must yield (even "") for every delta received, suppressed or not."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    deltas = ["<thought>", "still ", "reasoning ", "a while", "</thought>", "answer"]
    completions = _ScriptedStreamCompletions(deltas)
    provider = _make_provider(_FakeClient(completions))  # type: ignore[arg-type]

    yielded = [
        c async for c in provider.stream([Message(role="user", content="hi")], model="")
    ]

    # At least one item was yielded WHILE still inside the suppressed block
    # (not just the final "answer") — that's what resets the guard's clock.
    assert len(yielded) > 1
    assert "".join(yielded) == "answer"


@pytest.mark.asyncio
async def test_stream_yields_on_reasoning_content_only_chunk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Post gateway-fix (2026-07-16): NeraAiRaw now streams reasoning via its OWN
    `delta.reasoning_content` field, never inline in `delta.content` — so
    `delta.content` is empty/absent for the whole reasoning phase. Before this
    fix the loop silently `continue`d on every such chunk (no yield at all),
    which OwlResourceGuard's per-chunk timeout reads as a hung provider. A
    reasoning-only chunk must still yield "" to reset that clock."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)

    class _FakeReasoningDelta:
        def __init__(self, content: str | None, reasoning_content: str | None) -> None:
            self.content = content
            self.reasoning_content = reasoning_content

    class _FakeReasoningChoice:
        def __init__(self, content: str | None, reasoning_content: str | None) -> None:
            self.delta = _FakeReasoningDelta(content, reasoning_content)

    class _FakeReasoningChunk:
        def __init__(self, content: str | None, reasoning_content: str | None) -> None:
            self.choices = [_FakeReasoningChoice(content, reasoning_content)]
            self.usage = None
            self.model = "neraai-v1-raw"

    class _FakeReasoningStreamResponse:
        def __aiter__(self):  # noqa: ANN204
            return self._gen()

        async def _gen(self):  # noqa: ANN202
            yield _FakeReasoningChunk(None, "thinking")
            yield _FakeReasoningChunk(None, " some more")
            yield _FakeReasoningChunk("The answer", None)
            yield _FakeReasoningChunk(" is Paris.", None)

    class _FakeReasoningCompletions:
        async def create(self, **kwargs):  # noqa: ANN003, ANN201
            return _FakeReasoningStreamResponse()

    provider = _make_provider(_FakeClient(_FakeReasoningCompletions()))  # type: ignore[arg-type]

    yielded = [
        c async for c in provider.stream([Message(role="user", content="hi")], model="")
    ]

    # Two "" items reset the guard's clock during the reasoning-only chunks,
    # then the real content streams through untouched.
    assert yielded.count("") == 2
    assert "".join(yielded) == "The answer is Paris."


@pytest.mark.asyncio
async def test_stream_normal_prose_unaffected(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stream with NO whitespace-only deltas (a well-behaved endpoint) must
    pass through byte-identical — this normalization only touches the exact
    artifact shape, never real content."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    deltas = ["The", " answer", " is", " Paris", "."]
    completions = _ScriptedStreamCompletions(deltas)
    provider = _make_provider(_FakeClient(completions))  # type: ignore[arg-type]

    collected = [
        c async for c in provider.stream([Message(role="user", content="hi")], model="")
    ]

    assert "".join(collected) == "The answer is Paris."


def _feed_all(filt: _ThinkStreamFilter, deltas: list[str]) -> str:
    out = "".join(filt.feed(d) for d in deltas)
    return out + filt.flush()


def test_stream_filter_no_think_block_passes_through_immediately() -> None:
    # No <think> at all: each delta is emitted as soon as it's sniffed as safe.
    filt = _ThinkStreamFilter()
    assert filt.feed("Hello") == "Hello"
    assert filt.feed(" world") == " world"
    assert filt.flush() == ""


def test_stream_filter_strips_think_block_split_across_many_chunks() -> None:
    # live regression: stream() never called strip_think() at all — a reasoning
    # trace streamed straight to the user. This drives it char-by-char, the
    # worst case for a tag split across chunk boundaries.
    filt = _ThinkStreamFilter()
    text = "<think>reasoning here</think>final answer"
    assert _feed_all(filt, list(text)) == "final answer"


def test_stream_filter_think_and_answer_in_one_chunk() -> None:
    filt = _ThinkStreamFilter()
    assert _feed_all(
        filt, ["<think>deliberating</think>the answer"]
    ) == "the answer"


def test_stream_filter_leading_whitespace_before_think() -> None:
    filt = _ThinkStreamFilter()
    assert _feed_all(filt, ["  \n", "<think>x</think>", "answer"]) == "answer"


def test_stream_filter_case_insensitive_tags() -> None:
    filt = _ThinkStreamFilter()
    assert _feed_all(filt, ["<THINK>x</THINK>", "answer"]) == "answer"


def test_stream_filter_unclosed_think_drops_everything() -> None:
    # Truncated mid-thinking (output-cap hit) — matches strip_think()'s policy:
    # nothing after an unclosed <think> is real content, so drop it rather than
    # flush garbage reasoning to the user.
    filt = _ThinkStreamFilter()
    assert _feed_all(filt, ["<think>still reasoning", " when the cap hit"]) == ""


def test_stream_filter_short_reply_never_resolves_sniff_before_stream_ends() -> None:
    # A genuinely ambiguous prefix ("<" alone could still become "<think>") ends
    # the stream before enough chars arrived to resolve it — flush() must not
    # lose real content just because it happened to start with "<".
    filt = _ThinkStreamFilter()
    assert filt.feed("<") == ""  # still ambiguous — could be the start of <think>
    assert filt.flush() == "<"


def test_stream_filter_unambiguous_short_reply_passes_through_immediately() -> None:
    # "Hi" can never become "<think>" from its very first char, so it must not
    # be held back waiting for more input that will never disambiguate it.
    filt = _ThinkStreamFilter()
    assert filt.feed("Hi") == "Hi"


def test_stream_filter_strips_gemini_style_thought_block() -> None:
    # Live incident 2026-07-16: <thought> (Gemini-family) is a different tag
    # name than <think> (Qwen) — must be recognized too, split across chunks.
    filt = _ThinkStreamFilter()
    text = "<thought>planning the reply</thought>final answer"
    assert _feed_all(filt, list(text)) == "final answer"


def test_stream_filter_disambiguates_think_vs_thought_prefix() -> None:
    # "<thin" is ambiguous between "<think>" and could still resolve either
    # way as more chars arrive — must not misfire on the shorter tag's prefix.
    filt = _ThinkStreamFilter()
    assert _feed_all(
        filt, ["<though", "t>reasoning</thought>", "answer"]
    ) == "answer"


@pytest.mark.asyncio
async def test_complete_does_not_send_fixed_4096_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The artificial 4096 output cap is gone: with a resolved window the call's
    max_tokens reflects the window, not the flat default."""
    from stackowl.providers import model_window

    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    # Seed a resolved window for this (provider, model) so _output_cap uses it.
    monkeypatch.setitem(model_window._WINDOW_CACHE, ("ollama", "qwen3.5:2b"), 32768)
    completions = _ScriptedCompletions(["an answer"])
    provider = _make_provider(_FakeClient(completions))

    await provider.complete([Message(role="user", content="hi")], model="")

    assert completions.calls[0]["max_tokens"] == 32768  # window-derived, not 4096


@pytest.mark.asyncio
async def test_complete_caps_output_at_max_output_tokens_not_the_whole_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Live incident 2026-07-18: a large, correctly-resolved window (262144)
    used WHOLE as max_tokens leaves zero room for the prompt itself and 400s
    on every real call (ContextWindowExceededError) since the window is a
    TOTAL input+output ceiling, not an output-only budget. max_tokens must be
    bounded by the provider's max_output_tokens ceiling (default 250000,
    already documented as "generous") whenever the window exceeds it."""
    from stackowl.providers import model_window

    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    monkeypatch.setitem(model_window._WINDOW_CACHE, ("ollama", "qwen3.5:2b"), 262144)
    completions = _ScriptedCompletions(["an answer"])
    provider = _make_provider(_FakeClient(completions))

    await provider.complete([Message(role="user", content="hi")], model="")

    assert completions.calls[0]["max_tokens"] == 250000  # capped, not the raw 262144 window


@pytest.mark.asyncio
async def test_stream_does_not_send_fixed_max_output_tokens_when_window_resolves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """stream() was the one call path still using the raw max_output_tokens
    config value unconditionally instead of _output_cap()'s window-derived
    (usually smaller) budget — complete()/complete_with_tools() already did
    this. With a resolved window smaller than max_output_tokens, stream()'s
    request must reflect the window, matching test_complete_does_not_send_fixed_4096_cap."""
    from stackowl.providers import model_window

    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    monkeypatch.setitem(model_window._WINDOW_CACHE, ("ollama", "qwen3.5:2b"), 32768)
    completions = _ScriptedStreamCompletions(["an answer"])
    provider = _make_provider(_FakeClient(completions))  # type: ignore[arg-type]

    async for _ in provider.stream([Message(role="user", content="hi")], model=""):
        pass

    assert completions.calls[0]["max_tokens"] == 32768  # window-derived, not the flat config value


@pytest.mark.asyncio
async def test_stream_caps_output_at_max_output_tokens_not_the_whole_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mirrors test_complete_caps_output_at_max_output_tokens_not_the_whole_window
    for stream() — a large resolved window (262144) must still be bounded by
    max_output_tokens (250000), not requested whole (which would leave zero
    room for the prompt itself)."""
    from stackowl.providers import model_window

    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    monkeypatch.setitem(model_window._WINDOW_CACHE, ("ollama", "qwen3.5:2b"), 262144)
    completions = _ScriptedStreamCompletions(["an answer"])
    provider = _make_provider(_FakeClient(completions))  # type: ignore[arg-type]

    async for _ in provider.stream([Message(role="user", content="hi")], model=""):
        pass

    assert completions.calls[0]["max_tokens"] == 250000  # capped, not the raw 262144 window


class TestResolveModelOverride:
    def test_falls_back_to_provider_value_when_model_not_in_models_list(self) -> None:
        config = ProviderConfig(
            name="acme", protocol="openai", default_model="acme-v1",
            tiers=("fast",), max_output_tokens=250000, context_chars=None,
        )
        max_tokens, context_chars = resolve_model_override(config, "acme-v1")
        assert max_tokens == 250000
        assert context_chars is None

    def test_uses_model_override_when_set(self) -> None:
        config = ProviderConfig(
            name="acme", protocol="openai", default_model="acme-v1",
            tiers=("fast",), max_output_tokens=250000,
            models=(
                ModelOverride(
                    name="acme-v1-mini", tiers=("standard",),
                    max_output_tokens=50000, context_chars=80000,
                ),
            ),
        )
        max_tokens, context_chars = resolve_model_override(config, "acme-v1-mini")
        assert max_tokens == 50000
        assert context_chars == 80000

    def test_model_in_list_but_override_none_falls_back_to_provider_value(self) -> None:
        config = ProviderConfig(
            name="acme", protocol="openai", default_model="acme-v1",
            tiers=("fast",), max_output_tokens=250000,
            models=(ModelOverride(name="acme-v1-mini", tiers=("standard",)),),
        )
        max_tokens, context_chars = resolve_model_override(config, "acme-v1-mini")
        assert max_tokens == 250000
        assert context_chars is None


class _FakeToolFunction:
    def __init__(self, name: str, arguments: str) -> None:
        self.name = name
        self.arguments = arguments


class _FakeToolCall:
    def __init__(self, id: str, name: str, arguments: str) -> None:
        self.id = id
        self.type = "function"
        self.function = _FakeToolFunction(name, arguments)


class _FakeToolMessage:
    def __init__(self, content: str | None, tool_calls: list[_FakeToolCall] | None) -> None:
        self.content = content
        self.tool_calls = tool_calls


class _FakeToolChoice:
    def __init__(self, message: _FakeToolMessage) -> None:
        self.message = message


class _FakeToolResponse:
    def __init__(self, message: _FakeToolMessage) -> None:
        self.choices = [_FakeToolChoice(message)]
        self.model = "acme-v1-mini"
        self.usage = None


class _ToolThenWrapupCompletions:
    """1st call (carries ``tools=``) returns a native tool_use call; the 2nd call
    (the tool-free wrap-up round, forced by ``max_iterations=1``) returns a final
    answer. Records every call's kwargs so a test can assert the explicit
    ``model`` reaches BOTH internal API call sites in ``complete_with_tools``
    (Task 22) — the in-loop tool round AND the terminal wrap-up round — not just
    whichever one a trivial no-tool-call scenario happens to exercise."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> _FakeToolResponse:
        self.calls.append(kwargs)
        if kwargs.get("tools"):
            tc = _FakeToolCall("call_1", "noop_tool", "{}")
            return _FakeToolResponse(_FakeToolMessage(content=None, tool_calls=[tc]))
        return _FakeToolResponse(_FakeToolMessage(content="final wrap-up answer", tool_calls=None))


@pytest.mark.asyncio
async def test_complete_with_tools_uses_explicit_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Task 22 — complete_with_tools() must route an explicit ``model=`` kwarg
    to the outbound API call instead of always using the provider's
    ``default_model`` (the agentic tool-loop path gap this task closes)."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    completions = _ScriptedCompletions(["an answer"])
    provider = _make_provider(_FakeClient(completions))

    async def _dispatcher(name: str, args: dict[str, Any]) -> str:
        return "ok"

    await provider.complete_with_tools(
        user_text="hi",
        system_text=None,
        tool_schemas=[],
        tool_dispatcher=_dispatcher,
        model="acme-v1-mini",
    )

    assert completions.calls[0]["model"] == "acme-v1-mini"  # not the provider's default_model


@pytest.mark.asyncio
async def test_complete_with_tools_threads_model_to_every_call_site(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Task 22 — the SAME explicit ``model`` must reach EVERY internal API call
    site that can fire during a real multi-turn loop, not just the first one a
    trivial single-round scenario would exercise. Forces exactly two rounds via
    ``max_iterations=1`` with a tool call in round 1: the in-loop tool round
    (``tools=`` present) and the terminal wrap-up round (``tools=`` absent)."""
    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    completions = _ToolThenWrapupCompletions()
    provider = _make_provider(_FakeClient(completions))

    async def _dispatcher(name: str, args: dict[str, Any]) -> str:
        return "ok"

    text, _calls = await provider.complete_with_tools(
        user_text="hi",
        system_text=None,
        tool_schemas=[{"type": "function", "function": {"name": "noop_tool", "parameters": {}}}],
        tool_dispatcher=_dispatcher,
        max_iterations=1,
        model="acme-v1-mini",
    )

    assert len(completions.calls) == 2  # in-loop tool round + terminal wrap-up round
    assert completions.calls[0]["model"] == "acme-v1-mini"  # call site 1: the loop round
    assert completions.calls[1]["model"] == "acme-v1-mini"  # call site 2: the wrap-up round
    assert text  # never empty


@pytest.mark.asyncio
async def test_output_cap_uses_per_model_override(monkeypatch: pytest.MonkeyPatch) -> None:
    from stackowl.providers import model_window

    monkeypatch.setattr(TestModeGuard, "_active", False, raising=False)
    monkeypatch.setitem(model_window._WINDOW_CACHE, ("ollama", "acme-v1-mini"), 262144)
    config = ProviderConfig(
        name="ollama", protocol="openai", base_url="http://localhost:11434/v1",
        default_model="qwen3.5:2b", tiers=("fast",), max_output_tokens=250000,
        models=(
            ModelOverride(name="acme-v1-mini", tiers=("standard",), max_output_tokens=9000),
        ),
    )
    provider = OpenAIProvider(config, api_key="")
    assert provider._output_cap("acme-v1-mini") == 9000  # noqa: SLF001
    assert provider._output_cap("qwen3.5:2b") == 250000  # noqa: SLF001 — default_model, unaffected
