"""Model-aware charter E2E journey (Task 5) — small window → lean charter, large → full.

Two proofs that the model-aware charter wiring is live end-to-end:

JOURNEY 1 — small-window turn gets the LEAN charter (FR1)
  A secretary owl + a provider with context_chars=8000 (→ window 2000 ≤ 8192 =
  LEAN_WINDOW_THRESHOLD).  The assemble step must resolve window ≤ 8192 and embed
  behavioral_charter_lean() text in state.system_prompt.  The FULL-charter-only
  phrase "Act over assert: prefer doing the actual work" must NOT appear.

JOURNEY 2 — large-window control gets the FULL charter (FR3)
  Same harness but context_chars=320000 (→ window clamped to 16384).  The assemble
  step must resolve window ≥ 16384 and embed the full behavioral_charter() text.
  The lean-only phrase "Act and verify:" must NOT appear.

Gateway-driven: the real AsyncioBackend → GatewayScanner pipeline.  Only the AI
provider is mocked (a fake OpenAI SDK client that returns a canned reply).
state.system_prompt (and state.model_window) are read from the final PipelineState
returned by backend.run().
"""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.config.provider import ProviderConfig
from stackowl.config.test_mode import TestModeGuard
from stackowl.db.pool import DbPool
from stackowl.gateway.scanner import GatewayScanner, IngressMessage
from stackowl.owls.base_prompt import (
    LEAN_WINDOW_THRESHOLD,
    behavioral_charter,
    behavioral_charter_lean,
)
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.providers.base import CompletionResult, Message, ModelProvider
from stackowl.providers.openai_provider import OpenAIProvider
from stackowl.providers.registry import ProviderRegistry
from stackowl.tools.registry import ToolRegistry

# ---------------------------------------------------------------------------
# Context-chars constants (same values as the budget journey for consistency)
# ---------------------------------------------------------------------------

# 8000 chars → window 8000//4 = 2000 tokens → 2000 ≤ LEAN_WINDOW_THRESHOLD (8192)
_SMALL_CONTEXT_CHARS = 8_000

# 320000 chars → 80000 tokens → clamped to WINDOW_CEILING_DEFAULT = 16384 ≥ 16384
_LARGE_CONTEXT_CHARS = 320_000

# ---------------------------------------------------------------------------
# Distinctive text anchors
# ---------------------------------------------------------------------------

# This phrase exists ONLY in the full behavioral_charter() and NOT in the lean one.
# Full: "Act over assert: prefer doing the actual work with the capabilities …"
# Lean: "Act and verify: do the actual work …"
_FULL_ONLY_PHRASE = "Act over assert: prefer doing the actual work"

# This phrase exists ONLY in the lean behavioral_charter_lean() and NOT in the full one.
_LEAN_ONLY_PHRASE = "Act and verify:"

# ---------------------------------------------------------------------------
# Minimal triage/judge provider — routes to secretary; rules delivered=true
# ---------------------------------------------------------------------------


class _RouterJudgeProvider(ModelProvider):
    """Always routes to 'secretary'; always rules delivered=true on judge prompts."""

    @property
    def name(self) -> str:
        return "router-judge-fake"

    @property
    def protocol(self) -> Any:  # type: ignore[override]
        return "openai"

    async def complete(
        self, messages: list[Message], model: str, **kwargs: object
    ) -> CompletionResult:
        joined = "\n".join(m.content for m in messages)
        content = (
            '{"delivered": true, "reason": "ok"}'
            if "AGENT DRAFT REPLY" in joined
            else "secretary"
        )
        return CompletionResult(
            content=content,
            input_tokens=1,
            output_tokens=1,
            model="router-judge-fake",
            provider_name="router-judge-fake",
            duration_ms=0.0,
        )

    async def stream(  # type: ignore[override]
        self, messages: list[Message], model: str, **kwargs: object
    ):
        yield "secretary"


# ---------------------------------------------------------------------------
# Fake OpenAI SDK client (canned reply, no tool calls)
# ---------------------------------------------------------------------------


class _FakeMessage:
    def __init__(self, content: str | None, tool_calls: list[Any] | None = None) -> None:
        self.content = content
        self.tool_calls = tool_calls


class _FakeChoice:
    def __init__(self, message: _FakeMessage) -> None:
        self.message = message


class _FakeResponse:
    def __init__(self, message: _FakeMessage) -> None:
        self.choices = [_FakeChoice(message)]
        self.model = "gemma4:e4b"


class _FakeCompletions:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response

    async def create(self, **kwargs: Any) -> _FakeResponse:
        return self._response


class _FakeChat:
    def __init__(self, completions: _FakeCompletions) -> None:
        self.completions = completions


class _FakeClient:
    def __init__(self, response: _FakeResponse) -> None:
        self.chat = _FakeChat(_FakeCompletions(response))


# ---------------------------------------------------------------------------
# Provider + services factory
# ---------------------------------------------------------------------------


def _make_provider(context_chars: int) -> OpenAIProvider:
    config = ProviderConfig(
        name="ollama",
        protocol="openai",
        base_url="http://localhost:11434/v1",
        default_model="gemma4:e4b",
        tier="powerful",
        context_chars=context_chars,
    )
    response = _FakeResponse(_FakeMessage(
        content="Here is my answer to your question.",
        tool_calls=None,
    ))
    provider = OpenAIProvider(config, api_key="")
    provider._client = _FakeClient(response)  # type: ignore[assignment]
    return provider


def _build_services(
    provider: OpenAIProvider,
    owl_registry: OwlRegistry,
    tool_registry: ToolRegistry,
) -> StepServices:
    preg = ProviderRegistry()
    preg.register_mock("secretary", provider, tier="powerful")
    preg.register_mock("powerful", provider, tier="powerful")
    judge = _RouterJudgeProvider()
    preg.register_mock("router", judge, tier="fast")
    preg.register_mock("local-judge", judge, tier="local")
    return StepServices(
        provider_registry=preg,
        owl_registry=owl_registry,
        tool_registry=tool_registry,
    )


async def _drive_turn(
    backend: AsyncioBackend,
    scanner: GatewayScanner,
) -> PipelineState:
    """Drive one standard secretary turn end-to-end; return the final PipelineState."""
    msg = IngressMessage(
        text="What is the capital of France?",
        session_id="sess-charter-journey",
        channel="cli",
        trace_id="trace-charter-journey",
    )
    decision = scanner.scan(msg)
    input_text = decision.stripped_text if decision.stripped_text is not None else msg.text
    state = PipelineState(
        trace_id=msg.trace_id,
        session_id=msg.session_id,
        input_text=input_text,
        channel=msg.channel,
        owl_name=decision.target,
        pipeline_step="start",
        interactive=False,
    )
    return await backend.run(state)


# ---------------------------------------------------------------------------
# Autouse fixture — disable TestModeGuard + clear model-window cache
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _live_io_and_clear_cache(monkeypatch: pytest.MonkeyPatch):  # noqa: ANN202
    """Disable TestModeGuard and purge _WINDOW_CACHE so each test resolves fresh."""
    prev = TestModeGuard.is_active()
    TestModeGuard._active = False  # type: ignore[attr-defined]

    import stackowl.providers.model_window as mw
    monkeypatch.setattr(mw, "_WINDOW_CACHE", {})

    yield

    TestModeGuard._active = prev  # type: ignore[attr-defined]


# ===========================================================================
# JOURNEY 1 — small-window (2000 tokens ≤ 8192) → lean charter (FR1)
# ===========================================================================


async def test_small_window_gets_lean_charter(
    tmp_db: DbPool,
) -> None:
    """Small-window provider (2000-token window) → lean charter in system_prompt.

    FR1: a small-window/weak model must receive behavioral_charter_lean() — not
    the full charter that wastes tokens it doesn't have.

    Driven through the real gateway → GatewayScanner → AsyncioBackend.
    ONLY the AI provider is mocked.
    """
    tool_registry = ToolRegistry.with_defaults()
    owl_registry = OwlRegistry.with_default_secretary()
    provider = _make_provider(_SMALL_CONTEXT_CHARS)

    services = _build_services(provider, owl_registry, tool_registry)
    backend = AsyncioBackend(services=services)
    scanner = GatewayScanner(owl_registry=owl_registry)

    final_state = await _drive_turn(backend, scanner)

    # -----------------------------------------------------------------------
    # OUTCOME 1 — the turn produced a non-empty response (wiring is live).
    # -----------------------------------------------------------------------
    delivered = "".join(c.content for c in final_state.responses)
    assert delivered.strip(), (
        "CHARTER JOURNEY FAIL: the small-window turn produced no response — "
        "the pipeline is not wired correctly."
    )

    # -----------------------------------------------------------------------
    # OUTCOME 2 — state.model_window was stamped and is ≤ LEAN_WINDOW_THRESHOLD.
    # -----------------------------------------------------------------------
    assert final_state.model_window is not None, (
        "CHARTER JOURNEY FAIL (FR1): state.model_window is None — "
        "the assemble step did not resolve/stamp the window."
    )
    assert final_state.model_window <= LEAN_WINDOW_THRESHOLD, (
        f"CHARTER JOURNEY FAIL (FR1): model_window={final_state.model_window} > "
        f"LEAN_WINDOW_THRESHOLD={LEAN_WINDOW_THRESHOLD}; "
        "context_chars=8000 should give window=2000."
    )

    # -----------------------------------------------------------------------
    # OUTCOME 3 (FR1) — the lean charter text IS in state.system_prompt.
    # -----------------------------------------------------------------------
    system_prompt = final_state.system_prompt or ""
    lean_text = behavioral_charter_lean()
    # Use the first paragraph of the lean charter as the probe (robust to minor
    # whitespace differences at the join seam).
    lean_probe = lean_text.split("\n\n")[0]
    assert lean_probe in system_prompt, (
        f"CHARTER JOURNEY FAIL (FR1): lean charter opening paragraph not found in "
        f"system_prompt (window={final_state.model_window} ≤ {LEAN_WINDOW_THRESHOLD}).\n"
        f"Expected substring: {lean_probe!r}\n"
        f"system_prompt (first 500 chars): {system_prompt[:500]!r}"
    )

    # -----------------------------------------------------------------------
    # OUTCOME 4 (FR1) — the FULL-charter-only phrase is NOT in the prompt.
    # "Act over assert: prefer doing the actual work" only appears in full charter.
    # -----------------------------------------------------------------------
    assert _FULL_ONLY_PHRASE not in system_prompt, (
        f"CHARTER JOURNEY FAIL (FR1): full-charter-only phrase {_FULL_ONLY_PHRASE!r} "
        f"found in system_prompt even though window={final_state.model_window} — "
        "the lean path is not active."
    )


# ===========================================================================
# JOURNEY 2 — large-window (clamped to 16384) → full charter (FR3)
# ===========================================================================


async def test_large_window_gets_full_charter(
    tmp_db: DbPool,
) -> None:
    """Large-window provider (16384-token window) → full charter in system_prompt.

    FR3: a capable model must receive the full behavioral_charter() — the lean
    charter must NOT be used.

    Same harness, only context_chars differs.
    """
    tool_registry = ToolRegistry.with_defaults()
    owl_registry = OwlRegistry.with_default_secretary()
    provider = _make_provider(_LARGE_CONTEXT_CHARS)

    services = _build_services(provider, owl_registry, tool_registry)
    backend = AsyncioBackend(services=services)
    scanner = GatewayScanner(owl_registry=owl_registry)

    final_state = await _drive_turn(backend, scanner)

    # -----------------------------------------------------------------------
    # OUTCOME 1 — the turn produced a non-empty response.
    # -----------------------------------------------------------------------
    delivered = "".join(c.content for c in final_state.responses)
    assert delivered.strip(), (
        "CHARTER JOURNEY FAIL (FR3): the large-window turn produced no response."
    )

    # -----------------------------------------------------------------------
    # OUTCOME 2 — state.model_window was stamped and is ≥ 16384.
    # -----------------------------------------------------------------------
    assert final_state.model_window is not None, (
        "CHARTER JOURNEY FAIL (FR3): state.model_window is None — "
        "the assemble step did not stamp the window."
    )
    assert final_state.model_window >= 16384, (
        f"CHARTER JOURNEY FAIL (FR3): model_window={final_state.model_window} < 16384; "
        "context_chars=320000 should clamp to the ceiling (16384)."
    )

    # -----------------------------------------------------------------------
    # OUTCOME 3 (FR3) — the FULL charter text IS in state.system_prompt.
    # -----------------------------------------------------------------------
    system_prompt = final_state.system_prompt or ""
    full_text = behavioral_charter()
    full_probe = full_text.split("\n\n")[0]
    assert full_probe in system_prompt, (
        f"CHARTER JOURNEY FAIL (FR3): full charter opening paragraph not found in "
        f"system_prompt (window={final_state.model_window} ≥ 16384).\n"
        f"Expected substring: {full_probe!r}\n"
        f"system_prompt (first 500 chars): {system_prompt[:500]!r}"
    )

    # -----------------------------------------------------------------------
    # OUTCOME 4 (FR3) — the FULL-charter-only phrase IS in the prompt.
    # "Act over assert: prefer doing the actual work" only appears in full charter.
    # -----------------------------------------------------------------------
    assert _FULL_ONLY_PHRASE in system_prompt, (
        f"CHARTER JOURNEY FAIL (FR3): full-charter-only phrase {_FULL_ONLY_PHRASE!r} "
        f"NOT in system_prompt even though window={final_state.model_window} ≥ 16384 — "
        "the full charter is not being used for large-window models."
    )

    # -----------------------------------------------------------------------
    # OUTCOME 5 (FR3) — the lean-only phrase is NOT in the prompt.
    # -----------------------------------------------------------------------
    assert _LEAN_ONLY_PHRASE not in system_prompt, (
        f"CHARTER JOURNEY FAIL (FR3): lean-only phrase {_LEAN_ONLY_PHRASE!r} "
        f"found in system_prompt for a large-window model (window={final_state.model_window}) — "
        "the lean path must NOT fire for capable models."
    )
