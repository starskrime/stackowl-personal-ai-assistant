"""Context-budget E2E journey (Task 7) — per-model window sizing is real and truthful.

Two proofs of the per-model context-budget wiring:

JOURNEY 1 — small-window secretary turn presents a LEAN tool set (FR2/FR7)
  A secretary owl (no capability_profile) + ToolRegistry.with_defaults() + a
  provider whose context_chars is set to 8000 (→ 2000-token window).  The
  tool-token budget = 2000*0.9 − 2048 − fixed_cost < 0, so ONLY the guaranteed
  base (tool_search, tool_describe, read_file, …) fits.  We assert:
  * presented count is SMALL (≤ 15) and far below the full catalog count;
  * the non-evictable base contains read_file and tool_search;
  * the budget LOG line is truthful: tools_count == presented count and
    total_est_tokens ≥ tools_tokens > 0.

JOURNEY 2 — large-window control: ALL eligible tools are presented (FR5)
  Same setup but context_chars is huge (320000 → 80000-token window, clamped to
  WINDOW_CEILING_DEFAULT = 16384).  Budget is generous → the presented count is
  close to the full catalog count (≥ full catalog − 5 accounting for the default
  safety backstop 40-tool cap applied on the same set), far above the
  small-window case.

Gateway-driven: the real AsyncioBackend → GatewayScanner pipeline. Only the AI
provider is mocked (a fake OpenAI SDK client). The budget log is the primary
observation point because it captures `tools_count` from the *actual* budgeted
set — exactly the number handed to the provider.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from stackowl.config.provider import ProviderConfig
from stackowl.config.test_mode import TestModeGuard
from stackowl.db.pool import DbPool
from stackowl.gateway.scanner import GatewayScanner, IngressMessage
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.backends.asyncio_backend import AsyncioBackend
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.providers.base import CompletionResult, Message, ModelProvider
from stackowl.providers.openai_provider import OpenAIProvider
from stackowl.providers.registry import ProviderRegistry
from stackowl.tools.registry import ToolRegistry

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Small-window: 8000 chars → window_from_config(8000) = 8000//4 = 2000 tokens
# (clamped to WINDOW_CEILING_DEFAULT=16384; 2000 < 16384 so no clamp).
# tool_budget = 2000 * 0.9 − 2048 − fixed_cost < 0 → only guaranteed tools fit.
_SMALL_CONTEXT_CHARS = 8_000

# Large-window: 320000 chars → 80000 tokens → clamped to WINDOW_CEILING_DEFAULT=16384.
# tool_budget = 16384 * 0.9 − 2048 − fixed_cost ≈ 12646 − fixed → most tools fit.
_LARGE_CONTEXT_CHARS = 320_000

# The guaranteed base always present regardless of window (tool_search + tool_describe
# + read_file, write_file, shell, web_fetch, skill_manage, reflect_now,
# synthesize_skills, send_file, tool_build, memory).  We assert a subset.
_EXPECTED_BASE_NAMES = {"read_file", "tool_search"}

# Small-window upper bound on presented count.  The guaranteed base has ~12 tools
# (always_present=2 + base=10) — a tight budget may only fit those plus a few
# more.  Raised from 15 to 25 on 2026-07-22 (owner decision): tool_budget_tokens
# no longer shrinks the window by a 90% safety fraction + 2048-token reserve
# before fitting tools, so a small window now legitimately fits a few more —
# this bound proves the catalog (~76 tools) still was NOT dumped wholesale,
# not that the exact old-math count is preserved.
_SMALL_WINDOW_MAX = 25

# ---------------------------------------------------------------------------
# Minimal "triage/judge" provider — routes to secretary + accepts any answer
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
# Fake OpenAI SDK client — records tool_schemas handed to create()
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
    """Records the tool_schemas list handed to create() via kwargs['tools']."""

    def __init__(self, response: _FakeResponse) -> None:
        self._response = response
        # Each call appends the tool_schemas list received in that round.
        self.tool_schemas_per_call: list[list[dict[str, Any]]] = []

    async def create(self, **kwargs: Any) -> _FakeResponse:
        tools = kwargs.get("tools") or []
        self.tool_schemas_per_call.append(list(tools))
        return self._response


class _FakeChat:
    def __init__(self, completions: _FakeCompletions) -> None:
        self.completions = completions


class _FakeClient:
    def __init__(self, response: _FakeResponse) -> None:
        self.chat = _FakeChat(_FakeCompletions(response))


# ---------------------------------------------------------------------------
# Provider factory
# ---------------------------------------------------------------------------


def _make_provider(context_chars: int) -> tuple[OpenAIProvider, _FakeCompletions]:
    """Build a real OpenAIProvider with a fake client + the given context_chars.

    For the LARGE-window case we use protocol='openai' with no base_url AND a
    very large context_chars so resolve_window returns WINDOW_CEILING_DEFAULT
    (via config override path, which wins before the cloud-default path).
    """
    config = ProviderConfig(
        name="ollama",
        protocol="openai",
        base_url="http://localhost:11434/v1",
        default_model="gemma4:e4b",
        tier="powerful",
        context_chars=context_chars,
    )
    response = _FakeResponse(_FakeMessage(
        content="I can help with that — here is my answer.",
        tool_calls=None,
    ))
    fake_client = _FakeClient(response)
    provider = OpenAIProvider(config, api_key="")
    provider._client = fake_client  # type: ignore[assignment]
    return provider, fake_client.chat.completions


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
    preg.register_mock("standard-judge", judge, tier="standard")
    return StepServices(
        provider_registry=preg,
        owl_registry=owl_registry,
        tool_registry=tool_registry,
    )


async def _drive_turn(
    backend: AsyncioBackend,
    scanner: GatewayScanner,
) -> PipelineState:
    """Drive one standard (non-conversational) secretary turn end-to-end."""
    msg = IngressMessage(
        text="please summarize the latest research on neural scaling laws",
        session_id="sess-budget-journey",
        channel="cli",
        trace_id="trace-budget-journey",
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
    """Disable TestModeGuard and purge the module-level _WINDOW_CACHE so each test
    gets a fresh resolve_window call (no stale cached window from another test).
    """
    prev = TestModeGuard.is_active()
    TestModeGuard._active = False  # type: ignore[attr-defined]

    import stackowl.providers.model_window as mw
    monkeypatch.setattr(mw, "_WINDOW_CACHE", {})

    yield

    TestModeGuard._active = prev  # type: ignore[attr-defined]


# ===========================================================================
# JOURNEY 1 — small-window: presented set is LEAN (FR2/FR7)
# ===========================================================================


async def test_small_window_secretary_presents_lean_tool_set(
    tmp_db: DbPool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Small-window provider (2000-token window) → only guaranteed tools presented.

    FR2: the tool set is sized to the model's real window.
    FR7: the context-budget log is truthful.

    Driven through the real gateway → GatewayScanner → AsyncioBackend.
    ONLY the AI provider is mocked (fake OpenAI SDK client).
    """
    tool_registry = ToolRegistry.with_defaults()
    owl_registry = OwlRegistry.with_default_secretary()
    provider, completions = _make_provider(_SMALL_CONTEXT_CHARS)

    services = _build_services(provider, owl_registry, tool_registry)
    backend = AsyncioBackend(services=services)
    scanner = GatewayScanner(owl_registry=owl_registry)

    # Measure the FULL catalog count (no budget) for the comparison assertion.
    full_schemas = tool_registry.to_provider_schema("openai")
    full_count = len(full_schemas)

    with caplog.at_level(logging.INFO, logger="stackowl.engine"):
        final_state = await _drive_turn(backend, scanner)

    # -----------------------------------------------------------------------
    # OUTCOME 1 — the turn produced a non-empty response (wiring is live).
    # -----------------------------------------------------------------------
    delivered = "".join(c.content for c in final_state.responses)
    assert delivered.strip(), (
        "BUDGET JOURNEY FAIL: the turn produced no response — "
        "the pipeline is not wired correctly."
    )

    # -----------------------------------------------------------------------
    # OUTCOME 2 — the provider was actually called (tool schemas were handed to
    # create(); capture the presented set from the first call).
    # -----------------------------------------------------------------------
    assert completions.tool_schemas_per_call, (
        "BUDGET JOURNEY FAIL: the fake client's create() was never called — "
        "the tool loop did not execute."
    )
    presented_schemas = completions.tool_schemas_per_call[0]
    presented_count = len(presented_schemas)

    # -----------------------------------------------------------------------
    # OUTCOME 3 (FR2) — the presented count is SMALL (≤ _SMALL_WINDOW_MAX)
    # and strictly less than the full catalog.
    # -----------------------------------------------------------------------
    assert presented_count <= _SMALL_WINDOW_MAX, (
        f"BUDGET JOURNEY FAIL (FR2): small-window presenter handed {presented_count} "
        f"tool schemas to the provider — expected ≤ {_SMALL_WINDOW_MAX} (the budget "
        f"must limit the presented set when the window is tight). "
        f"Full catalog count: {full_count}."
    )
    assert presented_count < full_count, (
        f"BUDGET JOURNEY FAIL (FR2): presented_count ({presented_count}) == full_count "
        f"({full_count}) — the budget had NO effect; the full catalog was passed wholesale."
    )

    # -----------------------------------------------------------------------
    # OUTCOME 4 — the non-evictable base is present (read_file + tool_search).
    # -----------------------------------------------------------------------
    def _schema_name(s: dict[str, Any]) -> str:
        fn = s.get("function")
        body = fn if isinstance(fn, dict) else s
        return str(body.get("name", ""))

    presented_names = {_schema_name(s) for s in presented_schemas}
    for required in _EXPECTED_BASE_NAMES:
        assert required in presented_names, (
            f"BUDGET JOURNEY FAIL: guaranteed base tool '{required}' is missing from "
            f"the presented set {sorted(presented_names)!r} — the budget must NEVER "
            "evict the non-evictable base."
        )

    # -----------------------------------------------------------------------
    # OUTCOME 5 (FR7) — the budget LOG line is truthful.
    # caplog captures the [pipeline] execute: context budget log record.
    # -----------------------------------------------------------------------
    # The log record stores _fields via the `extra` kwarg injected by the logging
    # adapter.  Collect all budget records emitted on the tool-use path.
    budget_records_with_tools: list[logging.LogRecord] = []
    for r in caplog.records:
        if "[pipeline] execute: context budget" not in r.getMessage():
            continue
        fields = getattr(r, "_fields", None)
        if fields is None:
            continue
        if fields.get("tools_used") is not True:
            continue
        budget_records_with_tools.append(r)

    assert budget_records_with_tools, (
        "BUDGET JOURNEY FAIL (FR7): no '[pipeline] execute: context budget' log record "
        "with tools_used=True was emitted — the execute step did not log the budget. "
        f"All engine records: {[r.getMessage() for r in caplog.records if 'budget' in r.getMessage().lower()]!r}"
    )

    log_rec = budget_records_with_tools[0]
    fields = log_rec._fields  # type: ignore[attr-defined]
    log_tools_count = fields.get("tools_count")
    log_tools_tokens = fields.get("tools_tokens")
    log_total_est = fields.get("total_est_tokens")
    log_model_window = fields.get("model_window")

    # tools_count in the log must match the number of schemas the provider received.
    assert log_tools_count == presented_count, (
        f"BUDGET JOURNEY FAIL (FR7): log tools_count={log_tools_count} != "
        f"presented_count={presented_count} — the log is NOT truthful."
    )
    assert isinstance(log_tools_tokens, int) and log_tools_tokens > 0, (
        f"BUDGET JOURNEY FAIL (FR7): log tools_tokens={log_tools_tokens} must be > 0."
    )
    assert isinstance(log_total_est, int) and log_total_est >= log_tools_tokens, (
        f"BUDGET JOURNEY FAIL (FR7): log total_est_tokens={log_total_est} < "
        f"tools_tokens={log_tools_tokens} — impossible accounting."
    )
    assert isinstance(log_model_window, int) and log_model_window > 0, (
        f"BUDGET JOURNEY FAIL (FR7): log model_window={log_model_window} must be > 0."
    )


# ===========================================================================
# JOURNEY 2 — large-window control: presented set is FULL (FR5)
# ===========================================================================


async def test_large_window_presents_full_eligible_tool_set(
    tmp_db: DbPool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Large-window provider (clamped 16384-token window) → nearly full catalog.

    FR5: a capable model is NOT penalized by the budget logic; it sees the full
    eligible set (up to the HARD_TOOL_COUNT_CAP=40 count cap).

    Same harness, same secretary owl, same catalog, only context_chars differs.
    """
    tool_registry = ToolRegistry.with_defaults()
    owl_registry = OwlRegistry.with_default_secretary()
    provider, completions = _make_provider(_LARGE_CONTEXT_CHARS)

    services = _build_services(provider, owl_registry, tool_registry)
    backend = AsyncioBackend(services=services)
    scanner = GatewayScanner(owl_registry=owl_registry)

    full_schemas = tool_registry.to_provider_schema("openai")
    full_count = len(full_schemas)

    with caplog.at_level(logging.INFO, logger="stackowl.engine"):
        final_state = await _drive_turn(backend, scanner)

    delivered = "".join(c.content for c in final_state.responses)
    assert delivered.strip(), (
        "BUDGET JOURNEY FAIL: the large-window turn produced no response."
    )

    assert completions.tool_schemas_per_call, (
        "BUDGET JOURNEY FAIL: the fake client was never called in the large-window turn."
    )
    presented_schemas = completions.tool_schemas_per_call[0]
    large_presented_count = len(presented_schemas)

    # The HARD_TOOL_COUNT_CAP is 40; the full catalog exceeds that, so the
    # large-window cap clips at 40.  Assert presented ≥ 40 (or ≥ full_count if
    # the catalog is smaller than the cap — defensive).
    from stackowl.pipeline.context_budget import HARD_TOOL_COUNT_CAP

    expected_large_min = min(full_count, HARD_TOOL_COUNT_CAP)
    assert large_presented_count >= expected_large_min, (
        f"BUDGET JOURNEY FAIL (FR5): large-window presented only {large_presented_count} "
        f"tools — expected ≥ {expected_large_min} (min(full={full_count}, "
        f"hard_cap={HARD_TOOL_COUNT_CAP})). "
        "The budget must NOT constrain a large-window provider."
    )

    # The large-window count must be significantly larger than the small-window case.
    # Rerun the small-window count in isolation (different provider, same registry).
    small_provider, small_completions = _make_provider(_SMALL_CONTEXT_CHARS)
    small_services = _build_services(small_provider, owl_registry, tool_registry)
    small_backend = AsyncioBackend(services=small_services)

    import stackowl.providers.model_window as mw
    # Re-clear the cache so the small provider gets a fresh resolve.
    mw._WINDOW_CACHE.clear()

    with caplog.at_level(logging.INFO, logger="stackowl.engine"):
        await _drive_turn(small_backend, GatewayScanner(owl_registry=owl_registry))

    small_count = (
        len(small_completions.tool_schemas_per_call[0])
        if small_completions.tool_schemas_per_call
        else 0
    )

    assert large_presented_count > small_count, (
        f"BUDGET JOURNEY FAIL (FR5): large-window presented {large_presented_count} "
        f"tools but small-window presented {small_count} — "
        "the large window must present MORE tools than the small window."
    )
