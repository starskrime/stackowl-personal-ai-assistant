# Design Spec — Per-Model Context Budget + Lean Tool Presentation (Slice 1)

**Date:** 2026-06-13 · **Branch:** fresh off `main` (epic agentic-os-stage1 landed @3467565) · **Theme:** reliability spine — pillar ① tool execution + the weak-model "dummy answer" root cause. Slice 1 of a 3-slice arc (S1 tool budget + instrumentation, S2 answer-quality judge, S3 model-aware lean charter/DNA).
**Status:** approved design (brainstorming gate) → feeds implementation plan.
**Regression gate:** full `tests/journeys/` green + existing `tests/tools/` presentation + `tests/pipeline/steps/` execute tests green.

## Origin (live evidence, 2026-06-13 logs, model `qwen3.5:9b-mlx`)

Four real "standard" Telegram turns. The context-budget instrumentation logged `total_est_tokens: 3883` (system 1747 + memory 989 + history 2136), but the actual model call sent **~24,000 input tokens** (logged `23966in`, then `24627in`). The gap is **67 tool schemas ≈ ~20,000 tokens** (`tool_loop entry: {"tools": 67}`). A 9B model buried under 67 full JSON-Schemas produces shallow/"dummy" answers. The instrumentation never counted the schemas, so the bloat was invisible in the logs.

## Why the existing machinery missed it

`tools/_infra/presentation.py` already has a 31-tool cap, a guaranteed non-evictable base set, and a `tool_search` overflow meta-tool — **but only for owls with a non-empty `capability_profile`.** `registry.to_provider_schema` returns `self.all()` (the FULL catalog) when `profile is None and pins is None and hydrated is None` (registry.py ~259). The **secretary owl has no capability_profile**, so it bypasses the cap entirely and is handed every registered tool. There is also no notion of the model's real context window anywhere — the same presentation goes to a 9B local model and to a 200k-window cloud model alike. (Aggravating: the provider config drifted — `default_model: gemma4:12b-mlx` while the box actually served `qwen3.5:9b-mlx`.)

## Goal (this slice)

Make every turn's presented tool set fit the routed model's real context window, choosing the tools most relevant to the turn, and make the budget log tell the truth about token composition — so a weak/small-window model is no longer drowned in tool schemas. Charter/DNA leanness (S3) and the answer-quality judge (S2) are explicit follow-ups in the arc.

### Decisions (locked in brainstorming)
- **Window discovery — hybrid probe.** Probe the provider where possible (ollama `/api/show` exposes the model architecture's `context_length`); built-in defaults for known cloud models by name; a per-provider config field overrides both; fail-safe to a conservative default when the probe fails or the model is unknown. Matches the "capability-probe and scale to the host" rule.
- **Tool selection — window-cap + relevance fill.** A guaranteed base set is always present; discretionary slots are filled by the tools most RELEVANT to this turn's request; `tool_search` covers the long tail.
- **Relevance ranking — the existing lexical `rank_tools`** (`tools/meta/tool_search.py`), NOT embeddings. ADR-10 locks lexical/BM25 ranking at the registry layer (no embedding dependency); it is fast (no embedder latency on the Jetson), deterministic, multilingual (Unicode tokenizer), and never "unavailable".
- **Budget math — greedy fit by measured size.** Reserve headroom for charter + memory + history + a response allowance, then greedily fill tool schemas by their ACTUAL measured token size until the tool budget is spent. Keep full JSON-Schema per presented tool (the cap, not compaction, does the leaning — compaction is a later follow-up).
- **Apply to ALL owls.** Remove the no-profile full-catalog bypass so the budget governs the secretary too. An owl with no `capability_profile` is treated as "all groups eligible" for ranking — but still budget-capped.
- **Authoritative window for ollama.** The effective window `W` used for budgeting is also SENT to ollama as `options.num_ctx` (where the provider supports it) so the server honors exactly the window we budgeted — otherwise ollama silently truncates to its own `num_ctx` default and our budget is meaningless (the prior-known Ollama-truncation gap). `W` is bounded by a configurable ceiling so a large model's full context can't blow the host's RAM.

### Non-goals (explicit follow-ups)
- Tool-schema **compaction** (one-line signatures vs full JSON-Schema) — a later optimization; this slice leans by *count*, not by per-schema size.
- The **answer-quality judge** (Slice 2) and **model-aware lean charter/DNA** (Slice 3).
- Host-RAM auto-probing of a safe `num_ctx` ceiling — Slice 1 uses a configurable ceiling (default + per-provider override); RAM-aware auto-sizing is a follow-up.
- No DB / migration.

## Architecture

### A. Model-window discovery — `providers/model_window.py` (new)
A small, self-contained resolver: `resolve_window(provider, model, *, config_override) -> int`.
- **Order of precedence:** per-provider config field (`context_window`) > provider probe > built-in known-model default > fail-safe default (`DEFAULT_WINDOW_FALLBACK = 8192`).
- **Probe:** for an ollama-family provider, GET `{base_url}/api/show` (or POST per ollama API) and read the architecture `context_length`. Bounded by `WINDOW_CEILING_DEFAULT = 16384` (configurable per provider) so a 128k model doesn't blow the host's KV-cache RAM.
- **Known cloud defaults:** a small built-in name→window map for Claude/GPT families (kept tiny, defaulting generously; unknown cloud model → a safe large default).
- **Caching:** memoize per (provider_name, model) for the process; never re-probe per turn. The probe is best-effort and NEVER raises — any failure logs and returns the fail-safe default.
- The resolver does not import the providers package's heavy internals — it takes the provider's `base_url`/family and does a thin HTTP probe (reuse the existing http client utility if one exists; check before adding).

### B. The budgeter — `pipeline/context_budget.py` (new, pure)
`fit_tools(*, window, fixed_cost_tokens, base_tools, candidate_tools_ranked, measure) -> list[Tool]` — pure, deterministic, no I/O.
```
usable        = floor(window * PROMPT_SAFETY_FRACTION)          # 0.9
tool_budget   = usable - fixed_cost_tokens - RESPONSE_RESERVE_TOKENS   # 2048
# 1. base set is non-evictable; subtract its measured schema tokens (may push tool_budget < 0)
# 2. walk candidate_tools_ranked (already relevance-sorted); add each whose
#    measured schema size fits the remaining tool_budget; stop at exhaustion
#    or HARD_TOOL_COUNT_CAP (40) as a backstop.
# 3. tool_search is part of the base set (always present) → long-tail reachable.
```
`measure(tool) -> int` estimates a tool's serialized-schema tokens (~chars/4 of its provider-schema JSON). The budgeter returns the base set even when `tool_budget <= 0` (never present zero tools when tools are wanted).

### C. Presentation wiring — `tools/_infra/presentation.py` + `tools/registry.py` (extend)
- `to_provider_schema` gains optional `request_text: str | None` and `token_budget: BudgetInputs | None`. When a budget is supplied, it: builds the candidate pool (an owl's profile groups ∪ pins, or ALL groups when no profile), lexically ranks the non-base candidates against `request_text` via `rank_tools`, and calls `fit_tools`. When no budget is supplied, behavior is byte-identical to today (back-compat).
- **Remove the no-profile full-catalog bypass:** a no-profile owl now goes through the budgeted path (candidate pool = all groups), not `self.all()`.

### D. Execute wiring + instrumentation — `pipeline/steps/execute.py` (extend)
- Before building `tool_schemas`, resolve `W = resolve_window(provider, model, ...)`, measure `fixed_cost = est_tokens(system_prompt) + sum(est_tokens(history))` — `system_prompt` ALREADY includes the folded `memory_context` + charter + persona + skills (assemble.py joins them), so memory is NOT added separately (no double-count) — and pass `request_text=state.input_text` + the budget into presentation.
- For ollama-family providers, thread `W` as `options.num_ctx` into the provider call (check the existing provider options plumbing first; extend minimally).
- **Fix the context-budget log** (`[pipeline] execute: context budget`) to add `model_window`, `response_reserve`, `tools_count`, `tools_tokens` (measured from the final presented schemas), and a `total_est_tokens` that INCLUDES tools — so the log finally reflects the real prompt size and remaining headroom.

## Invariants
1. **Window-bounded:** the presented tool schemas' measured tokens never exceed the computed `tool_budget` (except the non-evictable base set, which is always present and logged when it alone exceeds the budget).
2. **Never zero tools:** a budgeted turn always presents at least the base set + `tool_search`.
3. **Back-compat:** with no budget supplied, `to_provider_schema` is byte-identical to today (the budget is opt-in at the call site; execute always supplies it).
4. **Relevance:** when the request lexically matches a non-base tool, that tool ranks ahead of unmatched ones for the discretionary slots (ties broken deterministically by `rank_tools`).
5. **Self-healing / no hidden errors:** window probe failure → logged fail-safe default; budgeter/resolver exception → fall back to the current full-presentation path, logged (never crash a turn).
6. **Truthful instrumentation:** the budget log's `total_est_tokens` includes tool schemas; `tools_tokens` is measured from the final presented set.
7. Language-agnostic: ranking uses the Unicode `rank_tools` tokenizer; no English keyword lists.

## Functional requirements (Given/When/Then)
- **FR1 (window discovery):** *Given* an ollama provider whose `/api/show` reports a context_length, *when* `resolve_window` runs, *then* it returns min(that, ceiling); *given* a config `context_window`, that overrides; *given* a probe failure/unknown model, *then* the fail-safe default (8192).
- **FR2 (budget cap):** *Given* a small window (e.g. 8k) and the full catalog, *when* presentation runs for a standard turn, *then* the presented schemas fit the tool budget (a small set, NOT 67/~20k), base set + `tool_search` always present.
- **FR3 (relevance fill):** *Given* a request whose keywords match a specific non-base tool, *when* the discretionary slots are filled, *then* that tool is presented (ahead of unmatched tools).
- **FR4 (secretary fix):** *Given* the secretary (no `capability_profile`), *when* a standard turn runs, *then* it is budget-capped (no full-catalog bypass).
- **FR5 (large window unchanged):** *Given* a large-window model, *when* presentation runs, *then* the full eligible set is presented (no regression) and the budget log shows headroom.
- **FR6 (num_ctx honored):** *Given* an ollama provider, *when* the model is called, *then* `options.num_ctx == W` is sent so the server honors the budgeted window.
- **FR7 (truthful log):** *Given* any tool turn, *when* the budget log emits, *then* `tools_tokens > 0`, `tools_count` matches the presented set, and `total_est_tokens` includes the tool schemas.
- **FR8 (fail-safe):** *Given* a window-probe error, *when* a turn runs, *then* it still completes (fallback window/full presentation), with a logged warning — never a crash.
- **FR9 (zero regression):** full `tests/journeys/` + existing presentation/execute tests stay green.

## Testing (gateway-driven, provider-mock-only)
- **budgeter units** (`pipeline/context_budget.py`): tiny window + large fixed cost → base set only; medium window → base + a bounded ranked subset by measured size; large window → all candidates; `HARD_TOOL_COUNT_CAP` backstop honored; `tool_budget <= 0` → base only (never empty).
- **window-discovery units**: ollama `/api/show` JSON parsed to context_length (mock the HTTP); config override wins; unknown/cloud default; probe exception → fail-safe default; ceiling clamp.
- **presentation units**: a no-profile owl is budget-capped (FR4); relevance puts a keyword-matched tool in the set (FR3); base set + `tool_search` always present; no-budget call is byte-identical (FR3 back-compat).
- **gateway journey (the live-bug regression):** a "standard" secretary turn through the real `AsyncioBackend` with a scripted provider + a small-window model → assert (a) presented `tools_count` is small and `tools_tokens` ≤ tool_budget (NOT ~67/~20k), (b) a request-relevant tool IS present, (c) `tool_search` present, (d) the budget log's `total_est_tokens` includes tools. Control: a large-window model → full eligible set, no regression.
- **num_ctx** unit: an ollama call carries `options.num_ctx == W` (FR6).
- Full `tests/journeys/` regression (FR9).

## House rules
Strict mypy; 4-point logging; no silent excepts (probe/resolver/budgeter all log + fail-safe); reuse `rank_tools` + the existing base-set/cap/`tool_search` machinery + any existing http client (check before adding); named constants (`DEFAULT_WINDOW_FALLBACK=8192`, `WINDOW_CEILING_DEFAULT=16384`, `PROMPT_SAFETY_FRACTION=0.9`, `RESPONSE_RESERVE_TOKENS=2048`, `HARD_TOOL_COUNT_CAP=40`); no DB/migration; no vendor names in `src/`; runtime state under `~/.stackowl/`; cross-platform.

## Rollback
Additive/gated: the budget is opt-in at `to_provider_schema`; reverting the execute call site (stop supplying the budget + request) restores today's full presentation. The new `model_window.py`/`context_budget.py` modules become dead but harmless; the instrumentation fields are pure-additive.

## Composition / dependency note
Foundation for Slice 3 (model-aware lean charter/DNA reuses `resolve_window`). Independent of Slice 2 (answer-quality judge). Builds on the landed reliability spine ([[project_reliability_spine_backlog]]); the intent-gated conversational bypass already zero-tools conversational turns — this slice governs the STANDARD path the bypass deliberately left untouched.

## Verification constraint
Unit + gateway tests cover the wiring now. Live "dummy answers stopped" verification (restart serve, ask a standard question, watch the now-truthful `context budget` log show a small `tools_tokens`) is DEFERRED until the model box `192.168.1.81` is reachable or a local model is pulled into the empty local ollama.
