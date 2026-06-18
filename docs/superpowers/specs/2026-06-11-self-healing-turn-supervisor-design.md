# Self-Healing Turn Supervisor — Design

**Date:** 2026-06-11
**Status:** Revised after party-mode stress-test; pending user spec-review → plan
**Branch:** feat/agentic-os-stage1

## Context

Three issues surfaced in production logs (2026-06-10/11). The deepest is behavioral: when a tool failed (a browser DNS error), the agent **gave up and returned no answer** instead of routing around the broken capability and still delivering. User framing: *"where is the self-healing agent… like a zombie — 30% of the brain works, but he refuses to use it because the other 70% errored."*

The intended outcome: a turn that, when a capability breaks, **routes around it** (executes an alternative that produces the same kind of result / answers from knowledge / builds the capability) and **never silently gives up or returns empty** — recover loudly, deliver what's possible, be honest about what failed.

**Party-mode reframed the design (Dr. Quinn).** The user's complaint is about **recovery** (capability substitution: route A breaks → use route B), not just **detection**. A nudge only *exhorts* a weak local model to "try harder" — it adds no capability and a sticky weak model re-attempts the same doomed path. So detection-hardening alone produces a *beautifully-formatted give-up* — the zombie, now articulate. Genuine self-healing needs a **recovery actuator** that does not depend on persuading the failed model.

**User decisions (this round):**
- **Active re-route = deterministic sibling execution.** Tools declare a capability tag; on a tool failure the supervisor itself executes an in-bounds sibling that produces the same kind of result and feeds the *result* back as a fresh observation (programmatic-first / deliver-the-result), not a re-prompt.
- **Structural net = always-on veto over the judge** (not just a fallback for judge errors) — it can override a confidently-wrong `DELIVERED` from a weak/hallucinating local judge.
- (Prior round) judge cascade with a local-model fallback; never-empty deterministic floor with the technical detail; full supervisor on all turns.

## Root causes (recon, file:line)

**Issue 3 — give-up / no self-healing (core):**
- Persistence judge is **fail-OPEN**: `pipeline/steps/execute.py:472–478` + `providers/anthropic_provider.py:186–192` — if the judge's own model call errors, `_enforce` returns `None` → give-up silently accepted. Weak local model → the judge is the likely failure point.
- **Judge can also LIE, not just error.** A weak local judge returns valid `{"delivered": true}` JSON on a real give-up — no exception, so today nothing rescues it (party-mode: Murat). This is the actual Jetson failure mode and the current design does not catch it.
- **Empty response reaches the user**: `providers/anthropic_provider.py:352–355` (wrap-up returns `""`), `pipeline/steps/execute.py:778–784` (hard exception → only `state.errors`, no response chunk). Also an **uncovered path** (Murat): a model nudged into a tool-loop that runs to `resolved_iterations` exhaustion with empty final text.
- **Enforcement narrowly gated**: `execute.py:451–452` (`interactive and delegation_depth == 0`).
- **Recovery actuator missing**: the only actuator today is "re-prompt the same model with more words." A detector wired to a powerless actuator (Dr. Quinn).

**Issue 1 — browser_browse "unhandled exception" on DNS:** `tools/browser/browse.py:228` (seed nav) and `:383` (inner `page.goto`) have no `try/except` → a foreseeable DNS error propagates raw to `tools/base.py:118` ("unhandled exception"). It IS wrapped into a failed `ToolResult` and fed back, but as a raw Playwright string; the ~6-min `duration_ms` vs the 30 s `_DEFAULT_NAV_TIMEOUT_MS` suggests a hung domain-slot.

**Issue 2 — memory `sqlite3.IntegrityError: datatype mismatch`:** `memory/dream_worker_helpers.py:~369,387` binds `str(uuid.uuid4())` to `audit_id INTEGER PRIMARY KEY AUTOINCREMENT` (the actual mismatch) and an ISO string to `timestamp REAL`. Canonical `scheduler/scheduler_helpers.py:write_audit` + `audit/logger.py:append` omit `audit_id` and bind `time.time()` (float) + `integrity_hash`. Pure query bug; no migration. Silently lost every consolidation run.

## Design principles (durable constraints)

- Build for **behavior** — model/infra-agnostic; no assumption of a specific model/tool/host.
- **Global/high-level** — fix by principle; **no DNS/browser/URL/example-specific logic** in the charter or supervisor. The alternative-set is **declared metadata the tools own**, not imperative logic in the supervisor (Dr. Quinn's reframe — "knowledge of alternatives" ≠ "tool-specific logic in the charter").
- **Self-extending** — a new tool joins the re-route set by declaring its capability tag; nobody edits the supervisor.
- **No hidden errors** — recover loudly; never return empty; never a silent degraded fallback.
- **No hardcoded English** — give-up signals are structural; user-facing fallback text uses the localization layer.
- **Self-healing everywhere** — detect, substitute, recover, continue, bounded.
- **Verify OUTCOMES not tool names**; require the escape-hatch before "impossible".
- **Consent/authz are inviolable** — a deterministic substitution must respect the consent gate + the owl's bounds/authz envelope (Epic-2). A substitution that would need consent is **surfaced as a named alternative**, never auto-executed.

## THE load-bearing invariant (Winston — the architecture)

> **The never-empty floor only ever writes to the `responses` channel. It never writes, clears, or mutates `errors`, `durable_parked`, or the A2A `status` field.** Success/failure classification stays owned by the existing structured signals; the floor changes only what the **user sees**, never what the **system concludes**.

Three downstream consumers infer success from text-presence/error-absence: the durable runner's status map (`task_runner.py:184–194`, `errors`-keyed), the A2A `status` envelope (`results.py`), and parliament synthesis. If the floor's honest-failure text leaks into a success-shaped channel, every self-healing failure silently becomes a fake success — the **inverted zombie**, and a violation of the "no hidden errors" rule. This invariant is the **first test**, and all merge-gate journeys assert the **structured outcome** (`status==failed`, `A2AResult.status != ok`, `errors` non-empty), never merely "response non-empty."

## Architecture — components

### 1. `CapabilitySubstitution` (the recovery actuator — NEW, the heart of the fix)

**Declarative metadata (tools own it):** each tool optionally declares a **capability tag** (e.g. `produces: web_knowledge`) and, for tools sharing a tag, a **normalized-input adapter** — how to build *this* tool's args from a normalized capability input (e.g. `web_target → web_search(query=…)` / `web_fetch(url=…)`). The adapter is the substitution-class contract; it lives in the tool/registry layer as data, keeping the supervisor generic (zero tool-specific strings).

**The actuator (fires on a tool-failure trajectory event, mid-loop — not at end-of-turn):** when a dispatch returns `TOOL_FAILED`, the supervisor:
1. asks the **registry** for in-bounds tools sharing the failed tool's capability tag (consulting the owl's bounds/authz envelope — Epic-2 — so a substitute is never out of bounds);
2. picks the highest-priority **safe** sibling. **Safety gate:** consequentiality is read from the **existing tool manifest `action_severity`** (the same signal `ledger_guard` uses at `execute.py:416`) — auto-execute only siblings whose severity is non-consequential / idempotent (read-class). A consequential sibling (one that would trip the consent gate) is NOT auto-run — instead its name is injected into the directive as a surfaced alternative (degrades to exhortation *with a concrete option* for that case);
3. for a safe sibling, **deterministically executes it** with args built via the normalized-input adapter, and feeds the **result** back to the model as a fresh observation;
4. **bounded:** at most one substitution attempt per failed capability per turn (no cascading doomed substitutions); charged against the BudgetGovernor; respects the same domain/rate slots.

If no tagged sibling exists (or all are consequential/out-of-bounds), there is no deterministic route — fall through to the detection+nudge path with the surfaced-alternative directive, then the floor. This is honest degradation, not silence.

### 2. `DeliveryGuard` (detection — hardened, always-on veto)

Replaces the fail-open `_enforce`. Lives in the **provider's `_enforce`** (`anthropic_provider.py:177`) — the per-turn tally and the judge try/except both live there (Amelia).

**Per-turn tally (Amelia — the strip-trap):** read the typed `failed` bool stored at `anthropic_provider.py:289` (`all_calls[i]["failed"]`). **NEVER re-scan `call["result"]` for `TOOL_FAILED_MARKER`** — it is stripped at `:286` before storage, so a re-scan is always-False and the net silently never fires. `tool_failures = sum(c["failed"])`, `successful = sum(not c["failed"])`.

**The structural signal (always computed, conservative):**
```
gave_up_structural = (tool_failures >= 1)
                     AND (successful_tool_calls == 0)
                     AND _structurally_irrelevant(draft)   # persistence.py:97 — pure, length-floor, language-agnostic
```
The `_structurally_irrelevant(draft)` clause (Amelia + Murat) kills the false-positive class — a substantive knowledge-answer / negative-result draft ("Paris", "No, the file doesn't exist") is NOT trivial → the net does not fire. The net only catches the genuine zombie shape: tool failed AND the draft is empty/stub.

**The cascade + veto (Murat — the veto is THE fix for the lying weak judge):**
1. **Primary judge** — `judge_delivery` on the normal model.
2. **Fallback judge** — on judge **error/timeout**, retry on the local always-available model.
3. **Structural veto (always-on)** — compute `gave_up_structural` regardless of judge outcome. If `gave_up_structural` is true AND the judge (primary or fallback) returned `DELIVERED`, **override to GAVE_UP**. Structural truth can contradict a hallucinated `DELIVERED`. If both judges are down, the structural signal stands alone.

**Bounded nudge with escalation-reward (Murat):** the nudge budget decrements **only when a nudge produced no new tool call** (re-refusal), not when the model escalated (called a new tool). Rewards genuine escalation, penalizes only sticky re-refusal. Cap stays small (default 2 non-escalating nudges).

**Never runs on already-terminal exits (Winston):** DeliveryGuard does NOT run on `TurnStopped` (a user-requested stop — nudging it ignores the stop) or `BudgetBreach` (a deliberate cost stop). A **live-steer message always pre-empts a give-up nudge** at the same iteration boundary (explicit ordering in `_compose_iter_cbs`).

### 3. `TerminalResponseGuarantee` (the last-resort never-empty floor)

A pure, deterministic, **no-model** synthesizer in a new `pipeline/supervisor.py`. The **last** resort — after substitution + nudges are exhausted — never the default recovery. Honors the load-bearing invariant (writes `responses` only).

**Two entry points (Amelia — `PipelineState` is out of scope inside the provider):**
- `synthesize_from_calls(goal, all_calls, partial) -> str` — for the provider empty-wrap-up (`anthropic_provider.py:355`), where `all_calls`/`messages`/`user_text` exist but `PipelineState` does not.
- `synthesize(state) -> str` — for `execute.py`'s hard-exception (`:778`) and empty-`final_text` exits, where `state` exists.

Slots via the **localization layer**: `{goal}`, `{failed_capability}`, `{attempts}`, `{partial}`, `{error}`. The error is the **truncated** technical string (`truncate_observation` at `:288` — the spec says truncated, not "verbatim"). The floor never raises; on any internal error it still returns a minimal localized non-empty string.

**Degraded hard-exception path (Amelia):** at `execute.py:778` this turn's tool records are lost (the exception fired before `raw_calls` was assigned). To populate `{attempts}`/`{failed_capability}` there, the provider attaches partial `all_calls` to the exception (mirror how `TurnStopped`/`BudgetBreach` already carry `tool_call_records`+`partial_text`). If absent, the floor degrades to `{goal}`+`{error}`+prior-`{partial}` — still non-empty and honest.

### 4. Enforcement surface (all turns) + delegation/parliament honesty

Drop the `interactive && depth==0` gate; the supervisor covers interactive + delegated + cron/parliament. **Delegation (Winston):** the floor feeds honest text **into** `_honest_failed_result` (`results.py`), which owns `status=success=False` — the floor never sets `status`, so a failed child can't be demoted to a fake `ok`. **Parliament:** a floored sub-owl failure is carried as structured status so synthesis degrades (synthesize from the owls that worked, note the gap) rather than averaging apologies.

### 5. Inline bug fixes

- **browser_browse:** wrap `goto` at `:228` and `:383` in `try/except`; classify by the **stable Playwright error code** (unknown-host / timeout / connection-reset / other — identifiers, not prose) into a structured handled-failure observation (no raw raise → no "unhandled exception" log). Investigate `acquire_domain_slot` for the 6-min hang; ensure the nav timeout bounds the call. (This tool also gains a `web_knowledge` capability tag so substitution can route around it.)
- **memory audit INSERT:** mirror `write_audit` — omit `audit_id`, `time.time()` float timestamp, include `integrity_hash`; reuse the canonical `_INSERT_AUDIT_SQL`. No migration.

## Placement (confirm via vote in the plan, per placement-voting rule)

- `DeliveryGuard` cascade + veto → **provider `_enforce`** (`anthropic_provider.py:177`); tally + judge try/except in scope.
- `CapabilitySubstitution` actuator → the **dispatch seam in `execute.py`** (`_dispatch`, which has `get_services()`/the registry); fires on a `TOOL_FAILED` dispatch result. Capability-tag metadata + normalized-input adapters → the tool/registry layer.
- `TerminalResponseGuarantee` → **new `pipeline/supervisor.py`** (pure, two entry points); invoked from the provider (empty-wrap-up) and `execute.py` (hard-exception / empty-`final_text`).
- Recon task (plan step 1) pins exact line numbers + the cross-layer callback plumbing (the fallback-tier model call; attaching `all_calls` to the hard exception).

## Error handling

Every new `except` logs (no silent catch); the cascade logs which tier/veto fired at debug. The browser classifier never re-raises navigation errors (only genuinely unexpected exceptions propagate). The substitution actuator, on its own failure, logs and falls through to the nudge path (never crashes the turn). The floor never raises.

## Testing

**Headline merge gate (Murat — the lying-judge test, proves the zombie is dead):** gateway test (mock only the AI provider) where (a) a tool returns `TOOL_FAILED`, (b) the model's draft is a polished give-up with no successful tool call, (c) the **judge model returns valid `{"delivered": true}`** (alive and WRONG). Assert: the structural veto fires → a substitution is attempted (or a re-route nudge) → the final user response is non-empty and names the blocked capability + what was tried. Every other test passes with the veto hole open; this one doesn't.

**Substitution (the recovery actuator):** a tagged tool fails → the in-bounds safe sibling is executed deterministically → its result reaches the model → the user gets a real answer (route-around proven). A consequential sibling is NOT auto-run (consent intact) but surfaced as a named alternative. No tagged sibling → honest degradation to nudge+floor.

**Idempotency triad (Murat — false-positive guards):** knowledge-answer-after-failed-search; file-not-found-is-the-answer; steer-abandoned-call — each asserts the final answer is **unchanged** after any spurious nudge (the net must not corrupt a correct answer).

**Floor + invariant:** hard provider exception → honest non-empty message AND `errors` still non-empty AND durable status still `failed` (the load-bearing invariant). Loop-exhaustion with empty final text → floor catches it. Delegated give-up → parent gets `A2AResult.status != ok` with non-empty honest text (not a fake `ok`).

**Exit-path safety:** `TurnStopped`/`BudgetBreach` exits unchanged (DeliveryGuard does not run; nudges charged against the budget); a live-steer pre-empts a pending give-up nudge.

**Bug fixes:** browser_browse at an unresolvable host → structured "unknown host" observation, no "unhandled exception" log, bounded by timeout. Contradiction path → a row lands in `audit_log` (no `datatype mismatch`), float timestamp, integer `audit_id`.

**Unit:** cascade tier/veto selection (lying judge → veto overrides; both down → structural stands); the strip-trap guard (tally reads `c["failed"]`, never re-scans); `_structurally_irrelevant` gating; the deterministic floor with zero providers; the normalized-input adapter per substitution class; browser error classification per code; the audit INSERT binding.

Targeted suites only (full suite hangs on this box): `tests/pipeline tests/providers tests/tools tests/memory` + the new journey tests.

## Build order (one effort, ordered workstreams — no deferral; cuts documented if any)

1. **Recon** — pin the seams (provider `_enforce`, the dispatch seam, the exit handlers, the cross-layer callbacks).
2. **Detection hardening** — DeliveryGuard cascade + always-on veto + strip-trap-safe tally + escalation-reward cap (provider `_enforce`).
3. **Never-empty floor** — `TerminalResponseGuarantee` (two entry points) + the load-bearing invariant + all empty-exit wiring (provider wrap-up, execute hard-exception/empty-final, loop-exhaustion).
4. **Recovery actuator** — capability-tag metadata + normalized-input adapters (start with the `web_knowledge` class: browser_browse/web_search/web_fetch) + the deterministic substitution at the dispatch seam + the consent/bounds safety gate.
5. **Enforcement surface** — all turns; delegation feeds floor into `_honest_failed_result`; parliament structured-status; steer pre-emption + budget charging.
6. **Bug fixes** — browser_browse goto + classification; memory audit INSERT.

## Out of scope / non-goals

- No tool-specific or example-specific logic in the supervisor/charter (capability knowledge is declared metadata).
- No new migration (issue 2 is a binding bug).
- Auto-execution of **consequential** substitutes (consent-bypass risk) — surfaced as named alternatives instead.
- Substitution arg-remapping across tools without a declared normalized-input adapter (only tagged classes with adapters substitute deterministically).
- Streaming/token-level delivery unchanged.
