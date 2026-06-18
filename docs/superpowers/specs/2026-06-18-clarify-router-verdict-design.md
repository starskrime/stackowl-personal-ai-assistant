# Design: `clarify` router verdict (incident P1 — the "prevent" half)

**Date:** 2026-06-18
**Branch:** `feat/clarify-router-verdict`
**Incident:** pictures-overclaim (`_bmad-output/incident-pictures-overclaim-fix-plan.md`, §P1)
**Status:** Approved design — implementation via subagent-driven TDD.

---

## 1. Problem

`SecretaryRouter` is a binary classifier: `intent_class ∈ {conversational, standard}`,
fail-safe to `standard`. A vague request that needs a consequential / capability-uncertain
action (e.g. **"can you help me with pictures"**) is forced down the tool-loop path, so a weak
model **guesses** the intent and spirals (9 failing shells → 120 s backstop → overclaim partial).
The same missing verdict also produced a false give-up floor on a bare **"hi"** (incident bug H).

The cure is a third verdict: when the request **under-determines the action** AND the resolved
interpretation needs an **expensive / irreversible / capability-uncertain** capability, ask **one**
clarifying question instead of entering the tool loop.

## 2. Principle (charter altitude)

Fire `clarify` on the **product** of ambiguity AND commitment-cost — never ambiguity alone. A
vague-but-cheap-and-reversible request ("summarize this") should still just **act**. Conservative
fail-safe: when in doubt between `standard` and `clarify`, choose `standard` (act). False-clarify is
annoying; over-acting on a clear-and-cheap request is the cheaper failure. A `clarify` verdict that
carries **no question** downgrades to `standard` (invariant: `clarify ⟹ question present`).

This is model- and host-agnostic (the build-for-behavior charter): the gating is meaning-based,
decided by the routing LLM via the (English-allowed) glue prompt — there are **no hardcoded English
keyword lists in code**. The class tokens (`conversational`/`standard`/`clarify`) are protocol labels.

## 3. Mechanism — Option A (router emits Q, deliver-and-yield)

Chosen over a restricted tool-loop because it **never enters any loop** (directly cures the spiral),
costs **no extra LLM hop**, and is deterministic.

1. **Router emits the question in the same fast-tier call.** Reply contract:
   - Line 1: owl name (unchanged).
   - Line 2: class token — now one of `conversational` / `standard` / `clarify`.
   - Line 3 (only when class is `clarify`): the ONE short clarifying question, in the user's language.
2. **triage** stamps `clarify_question` onto `PipelineState` alongside `intent_class`.
3. **execute.py** gains a dedicated early branch (before the `_use_tools` computation):
   - Register a **turn-yield** pending clarify via
     `clarify_gateway.ask(session_id, channel, question, blocking=False, deliver=False)`.
   - Emit the question as the turn's response `ResponseChunk` and return. The tool loop is never reached.
4. **Resume is already wired** (no new code): the user's next message →
   `ClarifyPump.resolve_or_rewrite` peeks the pending entry, classifies answer-vs-pivot, `try_resolve`
   pops it, and rewrites the message into a fresh resume turn
   (`"[Earlier you asked the user: …] The user's reply: …"`) which routes normally and acts.

### Why `deliver=False` + emit-as-response (not gateway-delivers + empty response)

Streaming the question as the real turn response routes it through the **same delivery/quality path
as a conversational turn** → non-empty buffer, `is_floor=False`, no give-up penalty, lands in the
transcript. `gateway.ask` otherwise *also* pushes the question via `adapter.send_clarify` →
double-send. So `ask` gains a single `deliver: bool = True` kwarg; the clarify branch passes
`deliver=False` (register-for-correlation only). One-line, in-purpose extension of the gateway —
not parallel infra. The pending registration is still required so `ClarifyPump` can correlate the
user's next message as the answer.

### Non-interactive contexts

In cron / parliament (no human to answer), a `clarify` verdict has no recipient. The execute branch
checks `TraceContext.interactive` (mirroring `ClarifyTool`) and, when not interactive, **falls
through to the standard tool path** — best-effort action rather than a question into the void.

## 4. Concrete changes (7 files, all small)

| File | Change |
|---|---|
| `pipeline/state.py` | Widen `intent_class` Literal → `+ "clarify"`; add `clarify_question: str \| None = None`; add `TOOL_FREE_CLASSES = frozenset({"conversational", "clarify"})` |
| `owls/router.py` | Widen `_VALID_CLASSES` + `RouteResult` Literal + `_parse_intent_class` return; add `clarify` to the glue prompt with the product gating + "line 3 = one question in the user's language"; parse the line-3 question; **downgrade `clarify`→`standard` if question empty**; carry `clarify_question` on `RouteResult` |
| `pipeline/steps/triage.py` | Stamp `clarify_question=result.clarify_question` in `state.evolve(...)` |
| `pipeline/provider_select.py` | `_ensure_tool_capable`: pass through when `intent_class in TOOL_FREE_CLASSES` (clarify needs no tools) |
| `pipeline/steps/execute.py` | New clarify early-branch (interactive only): register turn-yield pending + emit question chunk + return. Non-interactive → fall through to standard |
| `interaction/clarify_gateway.py` | Add `deliver: bool = True` to `ask`; skip `send_clarify` when `False` (still registers the entry) |
| `pipeline/steps/classify.py` + `assemble.py` | Treat `clarify` as lean (no heavy graph/skills) via `TOOL_FREE_CLASSES` — efficiency, the branch ignores assembled context |

## 5. Tests (gateway journeys — mock ONLY the AI provider, assert OUTCOMES)

Drive the REAL path; script the router/owl provider mocks, never mock the router decision itself.

1. **`test_vague_expensive_request_asks_one_question_not_tool_spiral`** — router-provider mock returns
   `secretary\nclarify\n<question>`; assert exactly one question surfaced, **zero tool calls**,
   `is_floor=False`, a pending clarify registered; a follow-up message resolves it and the resume
   turn acts. Load-bearing: the spiral path is not taken.
2. **`test_greeting_routes_conversational_no_floor`** (bug H) — "hi" → conversational, `is_floor=False`,
   no clarify, no tool loop.
3. **`test_vague_cheap_request_still_acts`** (falsification guard) — router mock → `standard` for a
   vague-but-cheap request → enters the tool path, does NOT clarify. Proves clarify fires only on the
   verdict, never always.
4. **Router unit tests** — 3-line clarify reply → `(clarify, question)`; clarify with empty line-3 →
   **downgrades to standard**; existing 2-line conversational/standard replies → byte-identical.

## 6. Invariants preserved

- `clarify ⟹ clarify_question` non-empty (parser-enforced).
- The give-up floor cannot fire on a clarify turn (zero tool calls ⟹ zero consequential failures).
- 2-line router replies and the conversational/standard paths stay byte-identical.
- No hardcoded English keyword lists in code; gating is meaning-based via the glue prompt.
