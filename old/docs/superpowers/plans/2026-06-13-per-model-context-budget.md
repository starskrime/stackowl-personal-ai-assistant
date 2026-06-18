# Per-Model Context Budget + Lean Tool Presentation (Slice 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Size each turn's presented tool set to the routed model's real context window (choosing the most relevant tools), and make the context-budget log count tool schemas — so a weak/small-window model (e.g. a 9B) is no longer handed all 67 tool schemas (~20k tokens) and stops giving dummy answers.

**Architecture:** A memoized window resolver (`model_window.py`: config `context_chars` override → ollama `/api/show` probe → cloud default → 8192 fallback, clamped to a 16384 ceiling) feeds a pure greedy size-fit budgeter (`context_budget.py`). `ToolPresentation` gains a relevance-ranked candidate ordering (reusing the lexical `rank_tools`); `to_provider_schema` serializes per-protocol, measures each schema, and includes tools greedily until the per-turn tool budget is spent over a non-evictable base set. `execute._run_with_tools` resolves the window, computes the fixed cost (system_prompt + history), passes the budget + request into presentation, and emits the truthful budget log. The OpenAI-compat provider reads the same memoized window to send `options.num_ctx` to ollama.

**Tech Stack:** Python 3.13, httpx (existing, used in `startup/provider_probe.py`), the existing `rank_tools`/`CatalogEntry` (`tools/meta/tool_search.py`), the existing `ToolPresentation` machinery, pytest.

---

## File Structure

| File | Create/Modify | Responsibility |
|---|---|---|
| `src/stackowl/providers/model_window.py` | **Create** | Resolve + memoize a model's effective context window (probe/override/default/fallback) |
| `src/stackowl/pipeline/context_budget.py` | **Create** | Pure greedy size-fit budgeter over a non-evictable base set |
| `src/stackowl/tools/_infra/presentation.py` | Modify | `rank_candidates`: guaranteed + relevance-ranked discretionary (no-profile → all eligible) |
| `src/stackowl/tools/registry.py` | Modify (`to_provider_schema`) | Opt-in `request_text`+`budget`: measure schemas, greedy-fit; route no-profile through the budgeted path |
| `src/stackowl/pipeline/steps/execute.py` | Modify | Resolve window, fixed cost, pass budget+request; truthful budget log in `_run_with_tools` |
| `src/stackowl/providers/openai_provider.py` | Modify | Inject `extra_body={"options":{"num_ctx":W}}` for ollama (read shared window cache) |
| `tests/providers/test_model_window.py` | **Create** | resolver units |
| `tests/pipeline/test_context_budget.py` | **Create** | budgeter units |
| `tests/tools/test_presentation_budget.py` | **Create** | rank_candidates + budgeted to_provider_schema units |
| `tests/journeys/test_context_budget_journey.py` | **Create** | gateway journey: small-window secretary turn is lean + truthful log |

---

## Task 1: `model_window.py` — resolve + memoize the effective window

**Files:**
- Create: `src/stackowl/providers/model_window.py`
- Test: `tests/providers/test_model_window.py`

**Context:** `ProviderConfig` (`config/provider.py`) already carries `base_url: str | None`, `default_model: str`, `name: str`, and `context_chars: int | None`. `startup/provider_probe.py` already uses `httpx.AsyncClient`. The resolver is async (it may probe), memoized per `(provider_name, model)`, and NEVER raises. A sync `cached_window` read lets the provider fetch the already-resolved value without re-probing.

- [ ] **Step 1: Write the failing test** `tests/providers/test_model_window.py`:

```python
import httpx
import pytest

from stackowl.providers import model_window as mw


@pytest.fixture(autouse=True)
def _clear_cache():
    mw._WINDOW_CACHE.clear()
    yield
    mw._WINDOW_CACHE.clear()


def test_config_override_wins_and_converts_chars_to_tokens():
    # context_chars is a CHAR budget; window is in TOKENS (~4 chars/token).
    w = mw.window_from_config(context_chars=40000)
    assert w == 10000  # 40000 // 4


def test_clamp_to_ceiling():
    assert mw._clamp(999_999) == mw.WINDOW_CEILING_DEFAULT  # 16384
    assert mw._clamp(4096) == 4096


async def test_resolve_uses_config_override_without_probing():
    # config_chars present → no probe, returns the override (clamped).
    w = await mw.resolve_window(
        provider_name="ollama", base_url="http://x:11434/v1",
        model="m", context_chars=40000, protocol="openai",
    )
    assert w == 10000
    assert mw.cached_window("ollama", "m") == 10000


async def test_resolve_probes_ollama_api_show(monkeypatch):
    # No config override → probe /api/show, read architecture context_length.
    class _Resp:
        def raise_for_status(self): ...
        def json(self):
            return {"model_info": {"qwen3.qwen.context_length": 32768}}

    class _Client:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, json):  # ollama /api/show is POST {name}
            assert url.endswith("/api/show")
            return _Resp()

    monkeypatch.setattr(mw.httpx, "AsyncClient", _Client)
    w = await mw.resolve_window(
        provider_name="ollama", base_url="http://x:11434/v1",
        model="qwen3.5:9b", context_chars=None, protocol="openai",
    )
    assert w == mw.WINDOW_CEILING_DEFAULT  # 32768 clamped to 16384


async def test_resolve_probe_failure_falls_back(monkeypatch):
    class _Boom:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **k): raise httpx.ConnectError("down")

    monkeypatch.setattr(mw.httpx, "AsyncClient", _Boom)
    w = await mw.resolve_window(
        provider_name="ollama", base_url="http://x:11434/v1",
        model="m", context_chars=None, protocol="openai",
    )
    assert w == mw.DEFAULT_WINDOW_FALLBACK  # 8192


async def test_cloud_default_for_anthropic_without_probe():
    w = await mw.resolve_window(
        provider_name="claude", base_url=None,
        model="claude-x", context_chars=None, protocol="anthropic",
    )
    assert w == mw.WINDOW_CEILING_DEFAULT  # cloud default clamped to ceiling


def test_cached_window_returns_none_when_absent():
    assert mw.cached_window("never", "seen") is None
```

> Read `startup/provider_probe.py` first to match the `httpx.AsyncClient` usage idiom and timeout style.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/providers/test_model_window.py -q`
Expected: FAIL — `ModuleNotFoundError: stackowl.providers.model_window`.

- [ ] **Step 3: Implement** `src/stackowl/providers/model_window.py`:

```python
"""Resolve a model's effective context window (tokens) for per-turn budgeting.

Precedence: per-provider config `context_chars` override → provider probe
(ollama /api/show) → known cloud default → conservative fallback. Clamped to a
ceiling so a huge-window model can't claim more KV-cache RAM than the host has.
Memoized per (provider_name, model). NEVER raises — any probe failure logs and
returns the fallback. A sync `cached_window` lets the provider read the already-
resolved value (to send num_ctx) without re-probing.
"""
from __future__ import annotations

import httpx

from stackowl.infra.observability import log

DEFAULT_WINDOW_FALLBACK = 8192
WINDOW_CEILING_DEFAULT = 16384
_CLOUD_DEFAULT = 200_000  # generous; clamped to the ceiling like everything else
_PROBE_TIMEOUT = 4.0

# Memoized per (provider_name, model). Process-lifetime; small.
_WINDOW_CACHE: dict[tuple[str, str], int] = {}


def _clamp(tokens: int) -> int:
    return max(1, min(int(tokens), WINDOW_CEILING_DEFAULT))


def window_from_config(*, context_chars: int) -> int:
    """Convert a configured CHAR budget to a TOKEN window (~4 chars/token), clamped."""
    return _clamp(context_chars // 4)


def cached_window(provider_name: str, model: str) -> int | None:
    """Sync read of an already-resolved window (None if not yet resolved)."""
    return _WINDOW_CACHE.get((provider_name, model))


async def _probe_ollama(base_url: str, model: str) -> int | None:
    """POST {base}/api/show {name: model}; read architecture context_length. None on any failure."""
    base = base_url.rstrip("/")
    # base_url is the OpenAI-compat root (…/v1); strip the trailing /v1 for the native API.
    if base.endswith("/v1"):
        base = base[: -len("/v1")]
    url = f"{base}/api/show"
    try:
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT) as client:
            resp = await client.post(url, json={"name": model})
            resp.raise_for_status()
            info = resp.json().get("model_info", {}) or {}
        # ollama keys context_length as "<arch>.context_length"; find the first match.
        for key, val in info.items():
            if key.endswith("context_length") and isinstance(val, int) and val > 0:
                return val
        return None
    except Exception as exc:  # never raise into a turn
        log.engine.debug(
            "[model_window] ollama probe failed",
            exc_info=exc, extra={"_fields": {"url": url, "model": model}},
        )
        return None


def _looks_like_ollama(base_url: str | None) -> bool:
    return bool(base_url) and (":11434" in base_url or "ollama" in base_url.lower())


async def resolve_window(
    *,
    provider_name: str,
    base_url: str | None,
    model: str,
    context_chars: int | None,
    protocol: str,
) -> int:
    """Resolve + memoize the effective window (tokens). Never raises."""
    key = (provider_name, model)
    cached = _WINDOW_CACHE.get(key)
    if cached is not None:
        return cached
    # 1. config override
    if context_chars is not None and context_chars > 0:
        w = window_from_config(context_chars=context_chars)
        log.engine.debug("[model_window] config override", extra={"_fields": {"model": model, "window": w}})
    # 2. probe (ollama-family only)
    elif _looks_like_ollama(base_url) and base_url is not None:
        probed = await _probe_ollama(base_url, model)
        w = _clamp(probed) if probed else DEFAULT_WINDOW_FALLBACK
        log.engine.info(
            "[model_window] resolved via probe",
            extra={"_fields": {"model": model, "probed": probed, "window": w}},
        )
    # 3. known cloud default
    elif protocol in ("anthropic", "openai", "gemini") and base_url is None:
        w = _clamp(_CLOUD_DEFAULT)
        log.engine.debug("[model_window] cloud default", extra={"_fields": {"model": model, "window": w}})
    # 4. fallback
    else:
        w = DEFAULT_WINDOW_FALLBACK
        log.engine.info("[model_window] fallback window", extra={"_fields": {"model": model, "window": w}})
    _WINDOW_CACHE[key] = w
    return w
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/providers/test_model_window.py -q` (all pass). `uv run mypy src/stackowl/providers/model_window.py` (clean). `uv run ruff check src/stackowl/providers/model_window.py`.

- [ ] **Step 5: Commit**

```bash
git add src/stackowl/providers/model_window.py tests/providers/test_model_window.py
git commit -m "feat(v2): model_window resolver — config>probe>default>fallback, memoized, never raises"
```

---

## Task 2: `context_budget.py` — pure greedy size-fit budgeter

**Files:**
- Create: `src/stackowl/pipeline/context_budget.py`
- Test: `tests/pipeline/test_context_budget.py`

**Context:** Pure function, no I/O. Given a window, the measured fixed cost (system_prompt + history tokens), a non-evictable list of guaranteed items, and a relevance-ranked list of discretionary candidates, return the subset that fits the per-turn tool budget. Generic over the item type via a `size_of` callable so it can be unit-tested with plain ints and reused with real `Tool`s.

- [ ] **Step 1: Write the failing test** `tests/pipeline/test_context_budget.py`:

```python
from stackowl.pipeline.context_budget import (
    HARD_TOOL_COUNT_CAP, PROMPT_SAFETY_FRACTION, RESPONSE_RESERVE_TOKENS,
    fit_items, tool_budget_tokens,
)


def test_tool_budget_subtracts_reserve_and_fixed_cost():
    # window 8192 * 0.9 = 7372; - reserve 2048 - fixed 1000 = 4324
    b = tool_budget_tokens(window=8192, fixed_cost_tokens=1000)
    assert b == int(8192 * PROMPT_SAFETY_FRACTION) - RESPONSE_RESERVE_TOKENS - 1000


def test_fit_keeps_all_guaranteed_even_when_over_budget():
    # guaranteed cost 500 each; budget only 300 → all guaranteed still returned, no discretionary.
    out = fit_items(
        guaranteed=["g1", "g2"], candidates=["c1", "c2"],
        budget=300, size_of=lambda _x: 500, hard_cap=HARD_TOOL_COUNT_CAP,
    )
    assert out == ["g1", "g2"]


def test_fit_adds_candidates_in_order_until_budget_spent():
    # budget 250; each item 100 → 2 candidates fit (200), 3rd (300) does not.
    out = fit_items(
        guaranteed=[], candidates=["c1", "c2", "c3"],
        budget=250, size_of=lambda _x: 100, hard_cap=HARD_TOOL_COUNT_CAP,
    )
    assert out == ["c1", "c2"]


def test_hard_cap_backstops_count():
    out = fit_items(
        guaranteed=[], candidates=[f"c{i}" for i in range(100)],
        budget=10_000_000, size_of=lambda _x: 1, hard_cap=5,
    )
    assert len(out) == 5


def test_guaranteed_consume_budget_before_candidates():
    # guaranteed 200 (1 item); budget 250 → 50 left → no 100-cost candidate fits.
    out = fit_items(
        guaranteed=["g"], candidates=["c"],
        budget=250, size_of=lambda x: 200 if x == "g" else 100, hard_cap=HARD_TOOL_COUNT_CAP,
    )
    assert out == ["g"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/pipeline/test_context_budget.py -q`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement** `src/stackowl/pipeline/context_budget.py`:

```python
"""Pure greedy size-fit budgeter for per-turn tool presentation.

Given the model's window and the measured fixed cost (system_prompt + history),
reserve response headroom, then greedily include a non-evictable `guaranteed`
set followed by relevance-ranked `candidates` until the tool-token budget is
spent (or a hard count cap is hit). Deterministic, no I/O. Generic over item
type via `size_of` so it is trivially unit-testable.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

PROMPT_SAFETY_FRACTION = 0.9
RESPONSE_RESERVE_TOKENS = 2048
HARD_TOOL_COUNT_CAP = 40

T = TypeVar("T")


def tool_budget_tokens(*, window: int, fixed_cost_tokens: int) -> int:
    """Tokens available for tool schemas this turn (may be <= 0 → base only)."""
    usable = int(window * PROMPT_SAFETY_FRACTION)
    return usable - RESPONSE_RESERVE_TOKENS - fixed_cost_tokens


def fit_items(
    *,
    guaranteed: list[T],
    candidates: list[T],
    budget: int,
    size_of: Callable[[T], int],
    hard_cap: int = HARD_TOOL_COUNT_CAP,
) -> list[T]:
    """Return guaranteed (always) + as many ranked candidates as fit by size/count.

    Guaranteed items are never dropped (they consume budget first; budget may go
    negative — discretionary then simply gets nothing). Candidates are walked in
    the given (relevance) order; each is added if it fits the remaining budget
    AND the total count is under `hard_cap`.
    """
    out: list[T] = list(guaranteed)
    remaining = budget
    for g in guaranteed:
        remaining -= size_of(g)
    for c in candidates:
        if len(out) >= hard_cap:
            break
        cost = size_of(c)
        if cost <= remaining:
            out.append(c)
            remaining -= cost
    return out
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/pipeline/test_context_budget.py -q` (pass). `uv run mypy src/stackowl/pipeline/context_budget.py` (clean). `uv run ruff check`.

- [ ] **Step 5: Commit**

```bash
git add src/stackowl/pipeline/context_budget.py tests/pipeline/test_context_budget.py
git commit -m "feat(v2): pure greedy size-fit tool budgeter (guaranteed base + ranked fill)"
```

---

## Task 3: `presentation.py` — relevance-ranked candidate ordering

**Files:**
- Modify: `src/stackowl/tools/_infra/presentation.py`
- Test: `tests/tools/test_presentation_budget.py`

**Context:** `ToolPresentation.select` (presentation.py:83) currently returns a COUNT-capped list. For budgeting we need it to expose the guaranteed set and the discretionary candidates in RELEVANCE order (so `to_provider_schema` can size-fit them). Add a method `rank_candidates` that returns `(guaranteed_tools, discretionary_ranked_tools)`. Discretionary = pins ∪ hydrated ∪ group-tools; when `profile` is None/empty, ALL non-guaranteed tools are eligible (fixes the secretary's full-catalog bypass). Ranking reuses the lexical `rank_tools`/`CatalogEntry` from `tools/meta/tool_search.py` (ADR-10: no embeddings); unmatched tools (score 0) keep a deterministic by-name tail so nothing is lost.

- [ ] **Step 1: Write the failing test** `tests/tools/test_presentation_budget.py`:

```python
from stackowl.tools._infra.presentation import ToolPresentation


class _FakeManifest:
    def __init__(self, group): self.toolset_group = group


class _FakeTool:
    def __init__(self, name, group="misc", desc=""):
        self.name = name
        self._g = group
        self.description = desc
        self.manifest = _FakeManifest(group)


def _tools():
    return [
        _FakeTool("read_file", "io", "read a file"),
        _FakeTool("write_file", "io", "write a file"),
        _FakeTool("tool_search", "meta"),
        _FakeTool("send_email", "comms", "send an email message"),
        _FakeTool("web_search", "search", "search the web"),
        _FakeTool("calendar_create", "calendar", "create a calendar event"),
    ]


def test_no_profile_makes_all_non_guaranteed_discretionary():
    # No profile → every non-base tool is an eligible candidate (secretary fix).
    guaranteed, disc = ToolPresentation().rank_candidates(
        all_tools=_tools(), profile=None, pins=None, hydrated=None, request_text="hello",
    )
    gnames = {t.name for t in guaranteed}
    assert "read_file" in gnames and "tool_search" in gnames  # base + always
    dnames = {t.name for t in disc}
    assert {"send_email", "web_search", "calendar_create"} <= dnames


def test_relevance_ranks_request_matched_tool_first():
    guaranteed, disc = ToolPresentation().rank_candidates(
        all_tools=_tools(), profile=None, pins=None, hydrated=None,
        request_text="please send an email to my boss",
    )
    # send_email (name+desc match "send"/"email") must outrank calendar/web for the discretionary slots.
    assert disc[0].name == "send_email"


def test_unmatched_tools_kept_in_deterministic_tail():
    guaranteed, disc = ToolPresentation().rank_candidates(
        all_tools=_tools(), profile=None, pins=None, hydrated=None,
        request_text="xyzzy-no-match",
    )
    # Nothing matches → all discretionary still present, ordered by name (deterministic).
    names = [t.name for t in disc]
    assert names == sorted(names)
    assert {"send_email", "web_search", "calendar_create"} <= set(names)
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/tools/test_presentation_budget.py -q`
Expected: FAIL — `AttributeError: 'ToolPresentation' object has no attribute 'rank_candidates'`.

- [ ] **Step 3: Implement.** Add to `ToolPresentation` in `src/stackowl/tools/_infra/presentation.py` (after `select`). Import `rank_tools`/`CatalogEntry` lazily inside the method to avoid a circular import (tool_search imports services):

```python
    def rank_candidates(
        self,
        *,
        all_tools: list[Tool],
        profile: list[str] | None,
        pins: list[str] | None,
        hydrated: set[str] | None,
        request_text: str | None,
    ) -> tuple[list[Tool], list[Tool]]:
        """Return (guaranteed, discretionary-ranked) for budgeted presentation.

        Guaranteed = always_present ∪ base (non-evictable). Discretionary =
        pins ∪ hydrated ∪ group-tools; when `profile` is falsy, ALL non-guaranteed
        tools are eligible (no full-catalog bypass). Discretionary is ordered by
        lexical relevance to `request_text` (reusing rank_tools); unmatched tools
        follow in a deterministic by-name tail so none are silently dropped.
        """
        from stackowl.tools.meta.tool_search import CatalogEntry, rank_tools

        cfg = self._cfg
        by_name = {t.name: t for t in all_tools}
        guaranteed_names = sorted(
            n for n in (cfg.always_present | cfg.base_tools) if n in by_name
        )
        guaranteed = [by_name[n] for n in guaranteed_names]
        gset = set(guaranteed_names)

        profile_groups = {g for g in (profile or []) if isinstance(g, str)}
        pin_names = {p for p in (pins or []) if isinstance(p, str)}
        hydrated_names = hydrated or set()

        def _eligible(t: Tool) -> bool:
            if t.name in gset:
                return False
            if not profile_groups and not pin_names and not hydrated_names:
                return True  # no profile → all non-guaranteed eligible
            return (
                t.name in pin_names
                or t.name in hydrated_names
                or t.manifest.toolset_group in profile_groups
            )

        candidates = [t for t in all_tools if _eligible(t)]
        # Relevance order via the lexical ranker; unmatched (score 0) excluded by
        # rank_tools, so append the remainder by name for a deterministic tail.
        ranked: list[Tool] = []
        if request_text:
            entries = [CatalogEntry(t.name, t.description, None) for t in candidates]
            hit_names = [e.name for e in rank_tools(entries, request_text, limit=len(entries))]
            order = {n: i for i, n in enumerate(hit_names)}
            ranked = sorted(
                candidates,
                key=lambda t: (order.get(t.name, len(order)), t.name),
            )
        else:
            ranked = sorted(candidates, key=lambda t: t.name)

        log.tool.debug(
            "[presentation] rank_candidates: exit",
            extra={"_fields": {
                "guaranteed": len(guaranteed), "candidates": len(ranked),
                "no_profile": not profile_groups,
            }},
        )
        return guaranteed, ranked
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/tools/test_presentation_budget.py -q` (pass). Then the existing presentation tests: `uv run pytest tests/tools/ -q -k "presentation"` (no regression — `select` is untouched). `uv run mypy src/stackowl/tools/_infra/presentation.py`. `uv run ruff check`.

- [ ] **Step 5: Commit**

```bash
git add src/stackowl/tools/_infra/presentation.py tests/tools/test_presentation_budget.py
git commit -m "feat(v2): ToolPresentation.rank_candidates — relevance-ranked discretionary (no-profile → all eligible)"
```

---

## Task 4: `registry.to_provider_schema` — budgeted, measured size-fit

**Files:**
- Modify: `src/stackowl/tools/registry.py` (`to_provider_schema`, lines 229-277)
- Test: extend `tests/tools/test_presentation_budget.py`

**Context:** `to_provider_schema` (registry.py:229) knows the protocol, so it can serialize each tool's schema and MEASURE it. Add opt-in `request_text` + `budget` params. When `budget` is supplied: call `rank_candidates`, build the per-protocol schema for guaranteed + ranked candidates, and `fit_items` by measured schema size. When `budget` is None: behavior is byte-identical to today (back-compat). The `restrict_to` path is unchanged (least-privilege planning wins over budgeting).

- [ ] **Step 1: Write the failing test** (append to `tests/tools/test_presentation_budget.py`). Use the REAL registry with a few registered tools; if constructing a registry is heavy, read `tests/tools/` for the existing registry fixture and reuse it. Sketch:

```python
from stackowl.pipeline.context_budget import tool_budget_tokens
from stackowl.tools.registry import ToolRegistry  # adjust import to the real one


def test_budgeted_schema_is_capped_and_back_compat(registry_with_many_tools):
    reg = registry_with_many_tools  # fixture: a ToolRegistry with > base+5 tools
    # No budget → full catalog (back-compat, byte-identical to today).
    full = reg.to_provider_schema("openai")
    # Tiny budget → only the guaranteed base set fits.
    tiny = reg.to_provider_schema(
        "openai", request_text="hello", budget={"window": 8192, "fixed_cost_tokens": 7000},
    )
    assert len(tiny) < len(full)
    names = {s["function"]["name"] for s in tiny}
    assert "read_file" in names and "tool_search" in names  # base always present
```

> First read `tests/tools/` for how a `ToolRegistry` is built in tests (fixture or inline). Reuse it; do NOT hand-roll a registry if a fixture exists.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/tools/test_presentation_budget.py -q`
Expected: FAIL — `to_provider_schema() got an unexpected keyword argument 'budget'`.

- [ ] **Step 3: Implement.** Modify `to_provider_schema` in `src/stackowl/tools/registry.py`. Add the two params and a budgeted branch BEFORE the existing `elif profile is None ...` fallthrough:

```python
    def to_provider_schema(
        self,
        protocol: str,
        *,
        profile: list[str] | None = None,
        pins: list[str] | None = None,
        hydrated: set[str] | None = None,
        restrict_to: frozenset[str] | None = None,
        request_text: str | None = None,
        budget: dict[str, int] | None = None,
    ) -> list[dict[str, object]]:
        # ... existing docstring ...
        def _schema_for(t: Tool) -> dict[str, object]:
            if protocol == "anthropic":
                return {"name": t.name, "description": t.description, "input_schema": t.parameters}
            return {
                "type": "function",
                "function": {"name": t.name, "description": t.description, "parameters": t.parameters},
            }

        if restrict_to is not None:
            from stackowl.tools._infra.presentation import ToolPresentation
            tools = ToolPresentation().select(
                all_tools=self.all(), profile=profile, pins=pins, hydrated=hydrated,
                restrict_to=restrict_to,
            )
            return [_schema_for(t) for t in tools]

        if budget is not None:
            import json
            from stackowl.pipeline.context_budget import fit_items, tool_budget_tokens
            from stackowl.tools._infra.presentation import ToolPresentation

            guaranteed, ranked = ToolPresentation().rank_candidates(
                all_tools=self.all(), profile=profile, pins=pins, hydrated=hydrated,
                request_text=request_text,
            )
            b = tool_budget_tokens(
                window=budget["window"], fixed_cost_tokens=budget["fixed_cost_tokens"],
            )

            def _size(t: Tool) -> int:
                return len(json.dumps(_schema_for(t))) // 4  # ~4 chars/token

            fitted = fit_items(guaranteed=guaranteed, candidates=ranked, budget=b, size_of=_size)
            return [_schema_for(t) for t in fitted]

        if profile is None and pins is None and hydrated is None:
            tools = self.all()
        else:
            from stackowl.tools._infra.presentation import ToolPresentation
            tools = ToolPresentation().select(
                all_tools=self.all(), profile=profile, pins=pins, hydrated=hydrated,
            )
        return [_schema_for(t) for t in tools]
```

(The `_schema_for` helper replaces the duplicated anthropic/openai list comprehensions at the bottom — DRY.)

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/tools/test_presentation_budget.py -q` (pass). Regression: `uv run pytest tests/tools/ -q` (the no-budget path is byte-identical; existing to_provider_schema tests stay green). `uv run mypy src/stackowl/tools/registry.py`. `uv run ruff check`.

- [ ] **Step 5: Commit**

```bash
git add src/stackowl/tools/registry.py tests/tools/test_presentation_budget.py
git commit -m "feat(v2): to_provider_schema opt-in token budget — measured size-fit over base + ranked (no-profile capped)"
```

---

## Task 5: `execute.py` — resolve window, budget the schemas, truthful log

**Files:**
- Modify: `src/stackowl/pipeline/steps/execute.py` (`_run_with_tools` ~410-452; the budget log ~1281-1293)
- Test: covered by Task 7's gateway journey + a targeted check below.

**Context:** In `_run_with_tools`, after computing `profile`/`pins`/`restrict_to`, resolve the window, compute the fixed cost, and pass the budget + request into `to_provider_schema`. Then emit the truthful budget log HERE (where `tool_schemas` exists). Gate the existing `run()` budget log to the no-tools path so a tool turn gets exactly one (truthful) line. `_est_tokens` already exists at module top.

- [ ] **Step 1: Implement (resolve + budget) in `_run_with_tools`.** Replace the `tool_schemas = tool_registry.to_provider_schema(...)` call (execute.py ~450-452) with:

```python
    # Per-model context budget: size the presented tool set to the model's real
    # window so a weak/small-window model is not drowned in tool schemas.
    from stackowl.providers.model_window import resolve_window
    _window = await resolve_window(
        provider_name=provider.name,
        base_url=getattr(provider, "_config", None) and provider._config.base_url,
        model=(getattr(provider, "_config", None) and provider._config.default_model) or "",
        context_chars=(getattr(provider, "_config", None) and provider._config.context_chars),
        protocol=provider.protocol,
    )
    _fixed_cost = _est_tokens(state.system_prompt) + sum(
        _est_tokens(getattr(m, "content", "")) for m in state.history
    )
    if restrict_to is not None:
        tool_schemas = tool_registry.to_provider_schema(
            provider.protocol, profile=profile, pins=pins, restrict_to=restrict_to
        )
    else:
        tool_schemas = tool_registry.to_provider_schema(
            provider.protocol, profile=profile, pins=pins,
            request_text=state.input_text,
            budget={"window": _window, "fixed_cost_tokens": _fixed_cost},
        )
    _tools_tokens = sum(len(__import__("json").dumps(s)) // 4 for s in tool_schemas)
    log.engine.info(
        "[pipeline] execute: context budget",
        extra={"_fields": {
            "trace_id": state.trace_id,
            "intent_class": state.intent_class,
            "tools_used": True,
            "model_window": _window,
            "response_reserve": 2048,
            "system_prompt_tokens": _est_tokens(state.system_prompt),
            "history_tokens": sum(_est_tokens(getattr(m, "content", "")) for m in state.history),
            "tools_count": len(tool_schemas),
            "tools_tokens": _tools_tokens,
            "total_est_tokens": _fixed_cost + _tools_tokens,
        }},
    )
```

> `restrict_to` (least-privilege planning) keeps its own un-budgeted presentation — planning already narrows the set, and it should not be widened by relevance fill. Budgeting applies only to the normal path.

- [ ] **Step 2: Gate the `run()` budget log to the no-tools path.** In `run()` (execute.py ~1281), wrap the existing `[pipeline] execute: context budget` emit so it only fires when tools are NOT used (the tool path now logs its own truthful line):

```python
    if not _use_tools:
        _sp_tokens = _est_tokens(state.system_prompt)
        _hist_tokens = sum(_est_tokens(getattr(m, "content", "")) for m in state.history)
        log.engine.info(
            "[pipeline] execute: context budget",
            extra={"_fields": {
                "trace_id": state.trace_id,
                "intent_class": state.intent_class,
                "tools_used": False,
                "system_prompt_tokens": _sp_tokens,
                "memory_context_tokens": _est_tokens(state.memory_context),
                "history_tokens": _hist_tokens,
                "total_est_tokens": _sp_tokens + _hist_tokens,
            }},
        )
```

> Read the exact current block (execute.py ~1279-1293) and the name of the `_use_tools` flag in `run()` first; match it. If the flag is named differently, use the real name.

- [ ] **Step 3: Run regression.** The execute behavior is unchanged except the presented set is now budgeted. Run: `uv run pytest tests/pipeline/steps/ -q` and `uv run pytest tests/pipeline/ -q -k "execute or budget"`. `uv run mypy src/stackowl/pipeline/steps/execute.py` (no NEW errors — there are pre-existing openai-SDK union errors elsewhere; only assert no new ones in execute). `uv run ruff check`.

- [ ] **Step 4: Commit**

```bash
git add src/stackowl/pipeline/steps/execute.py
git commit -m "feat(v2): execute budgets presented tools to the model window + truthful context-budget log"
```

---

## Task 6: ollama `num_ctx` — make the budgeted window authoritative

**Files:**
- Modify: `src/stackowl/providers/openai_provider.py` (the two `chat.completions.create` calls, ~263-268 and ~473-477)
- Test: `tests/providers/test_model_window.py` (add) or a small provider test

**Context:** Ollama silently truncates input to its own `num_ctx` default unless told otherwise — so without sending `num_ctx`, our budget is meaningless. The OpenAI-compat SDK forwards `extra_body` to the server. The provider reads the SHARED memoized window (warmed by Task 5's `resolve_window` before the call) via `cached_window`, and injects `extra_body={"options": {"num_ctx": W}}` ONLY for ollama-family base_urls. No signature change to `complete_with_tools` (avoids reopening the stale-double kwarg churn).

- [ ] **Step 1: Write the failing test** (add to `tests/providers/test_model_window.py` or a new `tests/providers/test_ollama_num_ctx.py`). Assert the provider builds `extra_body` with num_ctx when its base_url is ollama and the window is cached. Sketch (adapt to the provider's testability — read how other openai_provider tests fake `self._client`):

```python
async def test_ollama_create_carries_num_ctx(monkeypatch):
    from stackowl.providers import model_window as mw
    mw._WINDOW_CACHE[("ollama", "qwen3.5:9b")] = 12000
    # Build an OpenAIProvider with base_url ...:11434/v1, model qwen3.5:9b,
    # fake self._client.chat.completions.create capturing kwargs; assert it
    # received extra_body={"options": {"num_ctx": 12000}}.
    ...
```

> Read an existing `tests/providers/test_*openai*` to copy the fake-client harness; match it exactly rather than inventing one.

- [ ] **Step 2: Run to verify it fails** (no extra_body passed today).

- [ ] **Step 3: Implement.** In `openai_provider.py`, add a small helper + inject at both `create` calls:

```python
    def _ollama_extra_body(self, resolved_model: str) -> dict[str, Any]:
        """For an ollama-family base_url, send the budgeted window as num_ctx so the
        server honors exactly the window we budgeted (else ollama truncates to its
        own default). Empty dict for non-ollama providers / unknown window."""
        base = self._config.base_url or ""
        if ":11434" not in base and "ollama" not in base.lower():
            return {}
        from stackowl.providers.model_window import cached_window
        w = cached_window(self._name, resolved_model)
        return {"extra_body": {"options": {"num_ctx": w}}} if w else {}
```

Then at the main-loop create (~263) and the wrapup create (~473), spread it:

```python
                response = await self._client.chat.completions.create(
                    model=resolved_model,
                    messages=messages,  # type: ignore[arg-type]
                    max_tokens=self._config.max_output_tokens,
                    tools=tool_schemas,  # type: ignore[arg-type]
                    **self._ollama_extra_body(resolved_model),
                )
```

(and the same `**self._ollama_extra_body(resolved_model)` on the wrapup create, which has no `tools=`).

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/providers/ -q -k "num_ctx or model_window"` (pass). `uv run mypy src/stackowl/providers/openai_provider.py` (no NEW errors beyond the pre-existing SDK union ones). `uv run ruff check`.

- [ ] **Step 5: Commit**

```bash
git add src/stackowl/providers/openai_provider.py tests/providers/
git commit -m "feat(v2): send budgeted window as ollama num_ctx (authoritative, no signature churn)"
```

---

## Task 7: Gateway journey — small window is lean + truthful log + regression

**Files:**
- Create: `tests/journeys/test_context_budget_journey.py`

**Context:** The live-bug regression. Drive the REAL backend with a scripted provider whose `_config` reports an ollama base_url + a small window (via `context_chars`), for a secretary "standard" turn. Assert the presented tool set is SMALL (fits the budget — NOT ~67/~20k), a request-relevant tool IS present, `tool_search` is present, and the budget log reports `tools_tokens` + a `total_est_tokens` that includes tools. STUDY `tests/journeys/test_budget_cap.py` + `tests/journeys/test_self_heal_lying_judge.py` for the boot + scripted-provider + log-capture pattern; reuse it.

- [ ] **Step 1: Write the journey.** Scripted provider: capture the `tool_schemas` it receives in `complete_with_tools` (record `len(tool_schemas)` + the names), return a normal final answer in 1 round. Owl = secretary (no capability_profile). Provider `_config.context_chars` set small (e.g. 8000 chars → 2000-token window) so the budget is tight. Assert:
  - the captured `tool_schemas` count is SMALL (e.g. `<= 15`) and far below the full catalog count,
  - the guaranteed base (`read_file`, `tool_search`) is present,
  - a tool relevant to the scripted request is present (craft the request to match a non-base tool's name/description, assert it's in the captured set),
  - the `[pipeline] execute: context budget` log line for the turn has `tools_tokens > 0` and `total_est_tokens >= tools_tokens` (capture via `caplog`).

```python
# tests/journeys/test_context_budget_journey.py
# Boot mirrors test_self_heal_lying_judge.py (real OpenAIProvider w/ a fake _client +
# a small context_chars). Secretary owl (no profile). Scripted provider captures the
# tool_schemas it is handed. Assert: small count, base + a request-relevant tool present,
# tool_search present, and the budget log counts tools_tokens.
```

- [ ] **Step 2: Run; confirm it PASSES.** If the captured count is still large (full catalog), the budget isn't wired through — STOP, report BLOCKED (do not relax the assertion). `uv run pytest tests/journeys/test_context_budget_journey.py -q`.

- [ ] **Step 3: Add the large-window control (FR5).** Same setup but a large window (e.g. `context_chars=None` + a cloud protocol, or a big `context_chars`) → the full eligible set is presented (count ≈ catalog), proving no regression for capable models.

- [ ] **Step 4: Full regression (FR9).**

Run: `timeout 600 uv run pytest -q -p no:cacheprovider tests/journeys/`
Expected: prior green counts + the new journey's tests, ZERO failures/regressions. Watch `test_budget_cap`, `test_self_heal_*`, `test_p1_concurrent_foundation`, and the conversational-bypass journey especially. If any regress, STOP and report BLOCKED.

- [ ] **Step 5: Lint + commit.**

Run: `uv run ruff check tests/journeys/test_context_budget_journey.py`.

```bash
git add tests/journeys/test_context_budget_journey.py
git commit -m "test(v2): context-budget journey — small-window secretary turn is lean + truthful log (FR2/FR3/FR7), large window unchanged (FR5)"
```

---

## Self-Review (completed by plan author)

- **Spec coverage:** FR1 window discovery→T1; FR2 budget cap→T2+T4+T7; FR3 relevance fill→T3+T7; FR4 secretary fix→T3 (no-profile eligible) +T4+T7; FR5 large-window unchanged→T7 control; FR6 num_ctx→T6; FR7 truthful log→T5; FR8 fail-safe→T1 (probe never raises) + T5 (resolve_window never raises); FR9 regression→T7. All covered.
- **Placeholder scan:** T4-Step1 and T6-Step1 instruct the implementer to READ an existing test fixture/harness and reuse it (concrete "find this", not deferred work) — the assertion shape + the real code to add are fully specified. T5-Step2 says "read the exact current block + the `_use_tools` flag name" — concrete grounding, the replacement code is given. No TBD/TODO.
- **Type consistency:** `resolve_window(*, provider_name, base_url, model, context_chars, protocol) -> int` and `cached_window(provider_name, model) -> int | None` (T1) used consistently in T5/T6. `tool_budget_tokens(*, window, fixed_cost_tokens)` + `fit_items(*, guaranteed, candidates, budget, size_of, hard_cap)` (T2) used in T4. `rank_candidates(*, all_tools, profile, pins, hydrated, request_text) -> (list[Tool], list[Tool])` (T3) used in T4. `to_provider_schema(..., request_text, budget: dict[str,int])` (T4) called in T5 with `{"window","fixed_cost_tokens"}`. Constants `RESPONSE_RESERVE_TOKENS=2048`/`PROMPT_SAFETY_FRACTION=0.9`/`HARD_TOOL_COUNT_CAP=40` (T2) + `DEFAULT_WINDOW_FALLBACK=8192`/`WINDOW_CEILING_DEFAULT=16384` (T1). Consistent.

## Risk & containment
- **Risk:** `provider._config` private access in execute (T5) is brittle. **Contained:** guarded with `getattr(provider, "_config", None)`; on absence `resolve_window` gets None base_url/empty model → fallback window (8192). Consider a public `provider.config` property as a tiny follow-up.
- **Risk:** num_ctx via `extra_body` reopens the stale-double kwarg churn. **Contained:** T6 changes NO public signature — it reads the shared window cache inside the provider; test doubles are unaffected.
- **Risk:** budgeting wrongly drops a tool a turn needs. **Contained:** `tool_search` is always in the base set → the model reaches any dropped tool in one hop; T7 asserts relevance keeps the matched tool.
- **Risk:** `restrict_to` (planning) interaction. **Contained:** T5 keeps the un-budgeted presentation for the `restrict_to` path (planning already narrows; budgeting only applies to the normal path).
- **Rollback:** see spec — the budget is opt-in at `to_provider_schema`; reverting T5's call site restores today's full presentation.
