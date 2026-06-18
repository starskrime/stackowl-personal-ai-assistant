# Design Spec — Intent-Gated Lean Context (Conversational Bypass)

**Date:** 2026-06-12 · **Branch:** `feat/intent-gated-lean-context` (off `feat/agentic-os-stage1`) · **Theme:** the real root cause — a small local model is drowned by a model-blind ~24k-token prompt.
**Status:** approved design (brainstorming + BMAD party gate) → feeds implementation plan.
**Regression gate:** full `tests/journeys/` suite green (currently 91 passed / 1 skipped).
**Origin:** a live run showed a trivial "hi" produced a **~24,131-token** execute prompt and an 11-minute spiral, while the lean ~900-token triage call worked perfectly. A 5-persona BMAD party unanimously diagnosed model-blind context bloat (not the model). Verification confirmed: `trim_messages_to_budget` only elides message history (never the system prompt's tool schemas/charter/memory/skills/lessons), and its budget `CONTEXT_CHAR_BUDGET = 400_000` chars (~100k tokens) is hardcoded for huge models — so a small-model prompt is never trimmed; no `num_ctx` is sent to the server either.

## Problem

Every turn assembles a maximal, model-agnostic context (charter + operational adapter + DNA persona + presented tool schemas [subset of 111] + memory_context + skills block + cross-source lessons + history) and hands it to whatever model is routed. For a weak 12b model a ~24k-token prompt destroys instruction-following → it thrashes, calls random tools, and spirals. Triage already classifies cheaply and correctly, but **its output is only `owl_name` — there is no intent/complexity class**, so a "hi" goes through the same heavyweight tool-loop execute path as a complex task.

## Goal (this thin slice)

Make a trivial/conversational turn cost a tiny prompt and never enter the tool loop — fixing the "hi" disaster — and add the instrumentation to measure context composition. This is the first, highest-leverage step; the fuller per-model context budget for *standard* turns is an explicit follow-up.

### Decisions (locked in brainstorming + party)
- Reuse the router's existing cheap LLM call to emit a coarse intent class (no new model call if avoidable).
- **Fail-safe to `standard`:** `conversational` only on a clear phatic signal; any ambiguity/parse-miss/error → `standard` (full behavior). A misclassified *task* degrades to heavier, never tool-stripped.
- Conversational turns: zero tools (plain-stream path, no tool loop) + lean assembly (skip memory/skills/lessons/reflections/graph).
- Per-block context instrumentation, always (both classes), to measure.

### Non-goals (explicit follow-ups)
- Per-model **hard context budget** for *standard* turns (cap every block to the model's real window) — the next slice.
- Tool-schema compaction (one-line signatures vs full JSON-Schema) — follow-up.
- Modular charter; history sliding-window/summary; sending `num_ctx` to the provider — follow-ups (tracked).
- The paused bounded-turn and no-excuse-delivery slices remain valid as safety nets (kept; lower priority per the party).

## Architecture

### A. Intent classification (reuse the router call) → `PipelineState.intent_class`
Add `intent_class: Literal["conversational", "standard"] = "standard"` to `PipelineState` (default `standard` → every path that never sets it is byte-identical to today).

Primary mechanism: extend `SecretaryRouter` so its single fast-tier call returns BOTH the owl name AND a coarse class. The current prompt is name-only ("reply with ONLY the owl name") and intentionally minimal for weak-model robustness, so the change is conservative:
- The router asks for the owl name AND, on a second line, `conversational` or `standard` — `conversational` ONLY for greetings/phatic/chitchat with no task intent.
- Parsing is fail-safe: the owl-name parse is unchanged/first; if the class line is missing/unparseable → `standard`. The class NEVER affects which owl is chosen.
- `triage.run` stamps `intent_class` onto the state via `evolve`.

**Contingency (plan validates):** the plan's first task A/Bs routing quality with the 2-field prompt vs the current name-only prompt on a handful of cases. If the 2-field prompt measurably degrades routing on the weak model, fall back to a SEPARATE tiny binary classification call in `triage` (isolated from routing) — same `intent_class` output, same fail-safe-to-standard. Either way routing robustness is protected.

Direct-address turns (`owl_name != "secretary"`, router skipped) default to `standard` (unchanged) — acceptable; the bypass targets the common routed/secretary path.

### B. Lean assembly for conversational (`classify.run`)
When `state.intent_class == "conversational"`: skip `_gather_relevant_skills`, `_gather_lessons`, `_gather_recent_reflections`, `_gather_graph_context`, and heavy memory recall — leave `memory_context` minimal (prefs/short-context only or empty). The combine step omits those blocks. `standard` turns: unchanged (all blocks gathered as today).

### C. Zero-tools plain-stream execute for conversational (`execute.run`)
`execute.run` currently branches: `if tool_registry is not None and tool_registry.all(): return await _run_with_tools(...)` else a plain token stream. Gate that branch on the class:
```python
if state.intent_class != "conversational" and tool_registry is not None and tool_registry.all():
    return await _run_with_tools(state, provider, tool_registry)
# conversational → fall through to the plain-stream path: zero tools, no tool loop.
```
A conversational turn thus presents **zero tool schemas** and runs **no tool loop** — the spiral is impossible by construction, and the prompt is just (short) charter + persona + minimal memory + short history.

### D. Instrumentation (always, both classes)
At the execute call site, emit one structured log record with per-block sizes (chars + estimated tokens ≈ chars/4): `system_prompt_chars`, `tools_chars` (the serialized presented schemas, 0 for conversational), `memory_chars`, `history_chars`, `total_est_tokens`, plus `intent_class`. This confirms the breakdown and measures before/after. A small pure helper computes the estimate; never raises.

## Invariants
1. **Zero regression on standard:** `intent_class` defaults `standard`; every standard/unclassified turn assembles exactly as today.
2. **Fail-safe classification:** `conversational` only on a clear phatic signal; ambiguity/error → `standard`. Misclassified task → heavier, never tool-stripped.
3. **Routing robustness protected:** the class never changes owl selection; if the 2-field prompt degrades routing, fall back to an isolated binary call.
4. **No spiral on conversational:** zero tools + no tool loop ⇒ structurally cannot spiral.
5. No silent excepts; instrumentation never raises.

## Functional requirements (Given/When/Then)
- **FR1 (classify):** *Given* a clear greeting/chitchat, *when* triage runs, *then* `intent_class == "conversational"`; *given* a task ("send/search/create X") or any ambiguity/parse error, *then* `intent_class == "standard"`.
- **FR2 (lean assembly):** *Given* a conversational turn, *when* classify runs, *then* no skills/lessons/reflections/graph blocks are gathered (minimal memory_context).
- **FR3 (zero-tools):** *Given* a conversational turn, *when* execute runs, *then* zero tools are presented and the tool loop is NOT entered (plain-stream path).
- **FR4 (tiny prompt):** *Given* a conversational turn, *when* the model is called, *then* the execute prompt is a small fraction of the standard prompt (assert under a char/token budget, e.g. < 4k est-tokens) — verified via the instrumentation.
- **FR5 (no spiral / fast reply):** *Given* a conversational turn driven through the real backend, *when* it completes, *then* it delivers a direct reply without entering the tool loop (no nudge/spiral path).
- **FR6 (standard unchanged):** *Given* a standard turn, *when* it runs, *then* assembly + tool presentation are identical to today.
- **FR7 (routing intact):** *Given* the router's owl selection, *when* the class is added, *then* the chosen owl is unchanged from the name-only prompt on the test set.
- **FR8 (zero regression):** full `tests/journeys/` stays green.

## Testing (gateway-driven, provider-mock-only)
- Router/classifier unit: scripted router reply with owl+`conversational` → class conversational; owl+`standard` / owl-only / garbled class → `standard` (fail-safe); owl selection identical across cases (FR1/FR7).
- classify unit: conversational state → `_gather_*` not invoked / blocks absent (FR2).
- execute unit: conversational state + a registry with tools → asserts `_run_with_tools` is NOT taken (zero tools, plain-stream) (FR3).
- Instrumentation unit: per-block fields present + `tools_chars == 0` for conversational.
- Gateway journey (the live-bug regression): a "hi" turn through the real `AsyncioBackend` with a scripted provider → assert (a) no tool loop entered, (b) the instrumented `total_est_tokens` is under the budget (FR4), (c) a direct non-empty reply (FR5). A standard "do a task" turn → tools presented, tool loop entered, assembly unchanged (FR6).
- Full `tests/journeys/` regression (FR8).

## House rules
Strict mypy; 4-point logging; no silent excepts; no hardcoded English keyword lists for classification (the class comes from the LLM router, not a keyword match — preserves multilingual safety); reuse the router/classify/execute structure; named threshold constants. No DB/migration.

## Rollback
Additive/gated: `intent_class` defaults `standard`; revert = drop the router class output + the two gates (classify skip, execute branch) + the instrumentation. Standard turns are untouched throughout.

## Open value to confirm at spec review
The FR4 conversational prompt budget assertion threshold (proposed **< 4,000 est-tokens**, vs ~24k today).
