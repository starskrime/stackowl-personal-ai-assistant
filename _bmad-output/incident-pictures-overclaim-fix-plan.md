# Incident & Fix Plan — "can you help me with pictures" overclaim failure

**Date:** 2026-06-17 (logs in UTC 2026-06-18 01:51–03:16)
**Author:** forensic trace + BMAD party (Winston · Amelia · Murat · Dr. Quinn)
**Status:** PLAN ONLY — no code changes yet. For review.
**Evidence:** `~/.stackowl/logs/stackowl.jsonl`, `~/.stackowl/workspace/stackowl.db` (`task_outcomes`)
**Channel/session:** Telegram, session `72055773`, box provider = `ollama[fast]` (`qwen3.6:35b-mlx`)

---

## 1. What happened (the user-visible failure)

The user had a smooth conversation (algorithm mnemonics, resource links), then sent
**"can you help me with pictures"**. The turn (trace `d270584`) **failed**: `success=0`,
`failure_class=stop`, `quality_score=0.25`, 12 tool calls, **126.5 s** wall time.

The message delivered to the user was an **overclaim**:

> "Now let me create the visual diagrams as proper SVG image files — these are real image
> files that will look gorgeous on your phone! 🎨"

No images were ever produced. The assistant **lied about a deliverable**.

### Exact chain (from the log)

1. Router classified `intent_class=standard` (tool-using) — **not** a clarify path.
2. Weak model **guessed** the user wanted SVG diagrams of the *earlier* algorithm chat.
3. `execute_code` (~5 KB Python SVG generator) → sandbox **exit 1** (failed).
4. **9 consecutive `shell` calls, all failing** (Usage/error/tool_error/ERROR); malformed
   tool calls (missing `command`, missing `path`/`content`) correctly refused by pre-validation.
5. Persistence judge ruled **give-up twice** → 2 nudges.
6. At 126.5 s the **120 s default time backstop** fired
   (`DEFAULT_TURN_MAX_TIME_S=120.0`, `authz/bounds.py:50`):
   `[budget] gate: cap reached — stopping` → delivered a **partial**.
7. The honesty backstop (`critical_failure.py:148`) **does not apologize over a non-empty
   partial** ("a partial answer is NOT silence") → the overclaim shipped unmodified.

---

## 2. Root cause (layered) — symptom vs cause

| # | Finding | Causal role |
|---|---------|-------------|
| **A** | Honesty veto gates on **buffer emptiness**, not on **claim-vs-ledger truth**. A non-empty overclaim partial bypasses the floor. | **PRIMARY (user-visible lie).** `critical_failure.py:148` |
| **B** | Router has only **two verdicts** (`conversational` vs `standard`). No `clarify`. A vague × capability-uncertain request is forced to *act*, so the weak model guesses and spirals. | **PRIMARY (why it spiraled at all).** Also explains the false "hi" floor. |
| C | No **same-tool repeated-failure circuit breaker** — 9 identical failing shells burned budget to the 120 s wall. | Aggravating |
| D | **Provider tier degraded**: only `ollama[fast]` registered; `powerful` resolves to "first registered (degraded)" every turn. Whole tool loop runs on the weak model. | Aggravating (config) + architectural (silent degrade proceeds at full ambition) |
| E | Browser/picture capability physically down (missing `libx11-xcb`). | Environmental — removes the legitimate escape hatch |
| F | Embedding-model drift every turn (`corpus_embedding_model: null`) → recall degrades to FTS. | Systemic noise (separate P1) |
| G | "I don't like tables" preference saved to SQLite but **LanceDB upsert FAILED** (split-brain write). | Separate P1 (correctness/honesty) |
| H | Plain **"hi" hit the give-up floor** ("Sorry — I got tangled up… didn't finish cleanly", q=0.1). | Same root as B (missing `clarify` verdict / over-commit to "task") |

**The 120 s cap firing is correct and must stay** — a bounded turn is a durable invariant.
The flaw is what ships *at* the cap boundary, not that the boundary exists.

**Consensus rejection:** "put a stronger model on the box" is NOT the fix. A strong model
makes a *more confident, more plausible* wrong guess and hides the structural hole. The weak
model failed loudly and exposed it. Durable fixes must hold on every model and host
(per the build-for-behavior charter).

---

## 3. Fix plan (prioritized)

### P0 — Honesty veto on non-empty overclaim partials  *(contain)*
**Principle (charter altitude):** *A turn may only deliver text whose outcome-claims are
entailed by the committed ledger.* Silence and overclaim are both failures; emptiness was
only ever a proxy for one of them.

> **CORRECTION (after grounding the code 2026-06-17).** The party's pointer to
> `critical_failure.py:148` is WRONG. `detect_critical_failure` only fires when a CRITICAL
> step *errored* — but a budget cap is caught gracefully (`BudgetBreach`), so `execute`
> logs `step ok` and `critical_failure` never runs. The real mechanism is two converging
> defects in how the EXISTING honest-floor (`surface_consequential_giveup_floor`) is bypassed:

**D1 — the give-up predicate is masked by an irrelevant consequential success.**
`is_unachieved_consequential_giveup = (cons_failures >= 1 AND cons_successes == 0)`
(`persistence.py:120`). In the incident the model's final `write_file` SUCCEEDED (wrote an
SVG to disk the user never received), so `cons_successes >= 1` → predicate False →
`surface_consequential_giveup_floor` no-ops → overclaim ships. **A single trivial/irrelevant
consequential success disarms the honest floor**, even though the user's actual goal
(receive pictures) was never achieved.

**D2 — the `BudgetBreach` handler never stamps the consequential snapshot.**
`_snapshot_consequential` is called only on the normal return path (`execute.py:1506`). The
`BudgetBreach` handler (`execute.py:1246-1311`) returns the partial WITHOUT stamping the
snapshot, so the later `surface_consequential_giveup_floor` falls back to the live ledger,
which may already be torn down (the exact F099 hazard the snapshot mechanism was built to fix).

**Fix direction (needs a design decision — see §5a):**
- **`src/stackowl/pipeline/steps/execute.py` `BudgetBreach` handler** — stamp
  `_snapshot_consequential(state)` before returning, so the terminal honest-floor runs on
  immutable state, not a dead ledger. (Closes D2; low-risk, isolated.)
- **The discriminator for D1** is the crux: the masking success was a forward-looking artifact
  the user never received. Two candidate designs:
  - **(i) Forward-looking-claim guard (Winston Layer 2):** at a budget-cap exit, a partial
    that *promises* future work ("Now let me…") is structurally unsafe — the cap guarantees
    the promise won't complete. Ship the honest floor instead of the promise. Language-agnostic
    if keyed on "the turn did not reach a clean stop" rather than prose.
  - **(ii) Goal-relevant artifact accounting:** a consequential success only disarms the floor
    if it produced a **delivered** artifact (sent to the user / in the outbound envelope), not
    merely a file written to workspace. Requires distinguishing delivered vs incidental effects.
- **Do NOT** touch `is_unachieved_consequential_giveup`'s signature lightly — it is the SINGLE
  source of truth for the nudge veto AND the terminal floor (regressing it breaks the whole
  reliability arc). Any change needs the falsification guards in §4.

### P1 — `clarify` router verdict for ambiguity × commitment-cost  *(prevent)*
- Add a third verdict to the secretary router: when a request **under-determines the action**
  AND the resolved interpretation needs a **consequential / physically-uncertain** capability,
  ask **one clarifying question** instead of entering the tool loop.
- Gate on the **product** (ambiguity AND expensive/irreversible/capability-uncertain), never
  ambiguity alone — otherwise the assistant becomes needy. Vague-but-cheap-and-reversible
  ("summarize this") → just act.
- This also fixes **H** (the false "hi" floor) — same missing verdict, opposite tail.
- Touch the same router/classifier surface as the no-op-refusal L2 fix
  (`src/stackowl/owls/router.py`).

### P2 — Same-tool repeated-failure circuit breaker
- After **N consecutive failures of the same `capability_tag`**, stop offering that tool for
  the rest of the turn; don't burn budget to the wall. The 120 s cap is a backstop, not a
  spiral-termination strategy.

### P2 — Live quality-regression monitor on `task_outcomes`  *(detective)*
The telemetry already exists (`input_text`, `response_text`, `quality_score`, `tool_sequence`,
`failure_class` per turn) and is currently unharvested. Alert on the **conjunction**, not score
alone:
1. `failure_class=stop` **AND** deliverable-claim language **AND** empty/all-failed
   `tool_sequence` → overclaim signature (page).
2. `quality_score ≤ 0.1` on a greeting `input_text` → false-floor signature (distinct fix).
3. `corpus_embedding_model IS NULL` on any turn → config-drift canary (fires turn 1).
> A monitor *detects* (pages in minutes); only the P0 veto *prevents*. Build both.

### P2 — Capability-honest degradation (architecture)
- When the only available provider is weak/fast, feed the **existing lean-charter knob**
  (`select_tool_provider`, `state.model_window`): bias toward clarify, shrink the tool surface,
  raise the autonomous-action threshold. Prevents recurrence on **any** degraded host.

### Ops / config (band-aid, do on the box — not a regression test target)
- Register a real `powerful`-tier provider on the box (or relabel).
- Install browser libs (`libgtk-3-0 libx11-xcb1 libasound2`) if picture/screenshot is wanted.
- Per "never pin to Jetson": do **not** write a test that asserts the roster — test the
  *graceful degradation behavior* instead.

### Backlog (separate P1s, NOT this incident's causal path)
- **G** Split-brain memory write: SQLite-committed preference whose LanceDB upsert fails must
  not report fully-successful "saved" (no-hidden-errors rule). Gateway journey with vector
  store injected to fail.
- **F** Embedding-model drift: DB invariant / startup assertion + the monitor canary, not a
  mocked unit test.

---

## 4. Test strategy (Murat)

**Write these as gateway journeys (mock only the AI provider). Order:**

1. **`test_budget_cap_with_all_consequential_tools_failed_never_overclaims`** *(P0 guard)*
   - GIVEN "can you help me with pictures"; provider scripted to emit only **failing** tool
     calls + a final text claiming "...created the SVG image files..."; **squeeze the budget
     cap to ~5 s** (deterministic — never sleep 120 s); no artifact registered as produced.
   - THEN outbound MUST NOT assert a completed deliverable absent from the ledger;
     `failure_class=stop`; `quality_score` below the delivered threshold; user text is the
     honest floor (`is_floor=True`), NOT the optimistic partial; assert **no** "gorgeous on
     your phone".
   - **Load-bearing assertion:** outbound claim ⟂ ledger evidence.
   - **Drive the REAL backstop path** (squeeze the cap; do not mock the governor to "return
     give-up") — prior arcs shipped 4 production-breakers because green tests didn't drive the
     real path.
   - **Falsification guard (inverse):** a turn that genuinely produced an artifact must NOT be
     floored. Key on *evidence absent*, not *budget hit*.

2. **`test_greeting_routes_conversational_not_floor`** *(P1)* — GIVEN "hi" → plain reply,
   `is_floor=False`, high `quality_score`. (Regression — this class already bit us once.)

3. **`test_memory_write_lancedb_failure_not_reported_as_saved`** *(P1)* — vector store injected
   to fail → turn does not claim fully-successful save, OR a compensating path is taken.

**Do NOT** write tests that spin up a real LanceDB to "catch the drift" — chasing environment
with the wrong instrument; a flaky guard is worse than none.

---

## 5. The one tension (for your call)

- **Dr. Quinn:** the `clarify` verdict is the *cure*; the honesty veto is "a net under a cliff."
- **Murat:** the overclaim is the **sole P0** — it broke the truth-telling contract.

These are **defense-in-depth, not conflict**: `clarify` reduces how often you reach the cliff;
the veto guarantees you don't lie on the way down. Recommended sequence: **P0 veto first**
(smallest, highest-confidence, kills the user-visible lie), then **P1 clarify** (kills the
class). Everyone rejected "stronger model" as the headline fix.
