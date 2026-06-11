# Self-Healing Turn Supervisor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a tool/capability fails, the agent routes around it (deterministically executes an in-bounds sibling that produces the same kind of result), never silently gives up or returns empty, and stays honest about failures.

**Architecture:** Three cooperating units layered onto the existing ReAct loop, all reusing existing machinery. `DeliveryGuard` (detection — an always-on structural veto over a possibly-lying judge, in both providers' `_enforce`). `CapabilitySubstitution` (the recovery actuator — declarative capability-tag metadata + deterministic sibling execution at the dispatch seam). `TerminalResponseGuarantee` (a pure no-model never-empty floor bound by the load-bearing invariant: it writes only `responses`, never `errors`/`durable_parked`/A2A `status`). Shared logic lives in helpers so the two providers don't duplicate.

**Tech Stack:** Python 3.11+, asyncio, Pydantic v2, pytest, ruff, mypy --strict.

**Spec:** `v2/docs/superpowers/specs/2026-06-11-self-healing-turn-supervisor-design.md`

**Standing rules (every task):** TDD red→green; commit per task staging `v2/` only; every `except` logs; OOP; minimal code changes; NO hardcoded English (structural signals + localization only); consent/bounds inviolable; targeted tests only (full suite hangs — run specific paths, no `--timeout`). Run tests from `v2/`: `cd /ssd/projects/stackowl-personal-ai-assistant/v2 && uv run pytest <path> -v`. After each task: spec-compliance review → code-quality review → fix → commit.

---

## Recon facts (verified — use these exact seams)

- **Providers (TWO, identical pattern — share helpers):**
  - `src/stackowl/providers/anthropic_provider.py`: `_enforce` closure ~177–200; `nudge_budget = 2` at ~175, decrement ~194; fail-open `try/except → return None` ~182–192; Phase-D `if response.stop_reason != "tool_use":` ~236, `directive = await _enforce(text)` ~240; `all_calls.append({... "failed": failed})` ~289, `failed = TOOL_FAILED_MARKER in result_text` ~285, **strip-trap** `clean = result_text.replace(TOOL_FAILED_MARKER, "")` ~286; Phase-F wrap-up ~320–355, empty return `return "", all_calls` ~355.
  - `src/stackowl/providers/openai_provider.py`: `_enforce` ~197–225; `nudge_budget = 2` ~195; fail-open ~207–217; `all_calls.append` ~284, strip ~279–283; Phase-F wrap-up ~369–385.
- **`src/stackowl/pipeline/steps/execute.py`:** `_dispatch` 277–444 — success `return tr.output` ~437, failure `return f"{TOOL_FAILED_MARKER}{tr.error or tr.output}"` ~444, `t = tool_registry.get(name)` ~365, bounds `compute_effective_bounds`/`check_effective_bounds` ~311–353, `ledger_guard(name, args, t.manifest.action_severity, lambda: t(**args))` ~416. `_persistence_check(draft, tools_tried)` ~458–487, judge via `preg.get_with_cascade("fast")` ~468, `judge_delivery(...)` ~469–471, gate `if state.interactive and state.delegation_depth == 0:` ~452, fail-open `except → return None` ~472–478. Outer handlers: `DurableReplayUncertain` 687–712, `TurnStopped` 713–749 (carries `exc.tool_call_records`/`exc.partial_text`), `BudgetBreach` 750–777, bare `except Exception` 778–784 → `return state.evolve(errors=(...))`. Normal exit: `if final_text:` guard ~798, `return state.evolve(responses=..., tool_calls=...)` ~815. Provider returns only `final_text, raw_calls` (unpack ~684–686); `state` IS in scope, `all_calls`/`messages` are NOT.
- **`src/stackowl/pipeline/persistence.py`:** `TOOL_FAILED_MARKER = "\x00TOOL_FAILED\x00"` ~47; `PERSISTENCE_DIRECTIVE` ~53–58; `judge_delivery(provider, user_request, draft_answer, tools_tried) -> tuple[bool,str]` ~305; `_structurally_irrelevant(content) -> bool` ~97, `_MIN_RELEVANT_CHARS = 4` ~69; `summarize_tool_outcomes(all_calls) -> list[str]` ~195. `WRAPUP_DIRECTIVE` is in `src/stackowl/providers/_wrapup.py`.
- **`src/stackowl/setup/localize.py`:** `localize(key, lang="en") -> str` ~39, dict `_STRINGS: dict[tuple[str,str],str]` ~8–36. **No slot support — must add.**
- **`src/stackowl/tools/base.py`:** `ToolManifest` 25–57; `action_severity: Literal["read","write","consequential"] = "read"` ~33; existing `consent_category`, `toolset_group`, `commit_coupling`. **No capability tag — must add.**
- **`src/stackowl/tools/registry.py`:** `register(...)` ~160, `get(name) -> Tool|None`, `all()`. Tool has `@property manifest`.
- **Substitution targets:** `tools/browser/browse.py` manifest `action_severity="consequential"`, `toolset_group="browser"`, params `task`/`seed_url`/`allowed_domains`/`session_id`/`max_steps` (111–138); `tools/search/web_search.py` `action_severity="read"`, `toolset_group="web"`, params `query`/`limit` (50–76); `tools/io/web_fetch.py` `action_severity="read"`, params `url`/`mode` (73–86).
- **Delegation/durable:** `src/stackowl/tools/agents/results.py` `_honest_failed_result(record, msg, t0) -> ToolResult(success=False,...)` 144–158, status values in the record dict (`refused`/`child_error`/`uncertain`/...). Durable status keys off `errors` (execute.py handlers), not emptiness.
- **Bug sites:** `tools/browser/browse.py` seed `page.goto` ~228, inner `page.goto` ~383, `_DEFAULT_NAV_TIMEOUT_MS = 30_000` ~24, `acquire_domain_slot` ~227/382 (defined in `runtime`). `memory/dream_worker_helpers.py` `_INSERT_AUDIT_SQL` 116–119 (6-col incl. `audit_id`); canonical `scheduler/scheduler_helpers.py` `_INSERT_AUDIT_SQL` 38–41 (5-col, no audit_id) + params `(event_type, actor, target, time.time(), payload)` 68–74; `audit/logger.py` append 111–118 (6-col incl. `integrity_hash`). Schema `db/migrations/0027_*.sql`: `audit_id INTEGER PRIMARY KEY AUTOINCREMENT`, `timestamp REAL`, `integrity_hash TEXT`.
- **Tests:** journeys in `tests/journeys/test_j*.py`; mock ONLY the AI provider (a scripted `complete_with_tools` with `protocol` property driving the real tool loop). Unit dirs: `tests/pipeline/`, `tests/providers/`, `tests/tools/{browser,search,io}/`, `tests/memory/`.

---

## File structure

| File | Responsibility | Action |
|---|---|---|
| `src/stackowl/pipeline/supervisor.py` | `TerminalResponseGuarantee` (pure floor, 2 entry points); `apply_structural_veto`; `tally_tool_outcomes` | Create |
| `src/stackowl/pipeline/capability_substitution.py` | capability-class registry + normalized-input adapters + `find_substitute` | Create |
| `src/stackowl/pipeline/persistence.py` | reuse rubric; export `is_structural_giveup` | Modify |
| `src/stackowl/providers/anthropic_provider.py` | wire veto into `_enforce`; floor into empty wrap-up; attach `all_calls` to exceptions | Modify |
| `src/stackowl/providers/openai_provider.py` | same wiring (via shared helpers) | Modify |
| `src/stackowl/pipeline/steps/execute.py` | fallback judge tier; drop gate; substitution at dispatch; floor at hard-exception/empty-final | Modify |
| `src/stackowl/setup/localize.py` | add `localize_format` + floor catalog keys | Modify |
| `src/stackowl/tools/base.py` | add `capability_tag` to `ToolManifest` | Modify |
| `src/stackowl/tools/{browser/browse,search/web_search,io/web_fetch}.py` | declare `capability_tag="web_knowledge"`; browse goto guard | Modify |
| `src/stackowl/tools/agents/results.py` | floor text feeds into honest-failure builders | Modify |
| `src/stackowl/memory/dream_worker_helpers.py` | audit INSERT fix | Modify |

---

# Workstream 1 — Detection hardening (DeliveryGuard)

### Task 1: Shared tool-outcome tally + structural give-up signal

**Files:**
- Create: `src/stackowl/pipeline/supervisor.py`
- Modify: `src/stackowl/pipeline/persistence.py` (export `is_structural_giveup`)
- Test: `tests/pipeline/test_supervisor_tally.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/pipeline/test_supervisor_tally.py
from stackowl.pipeline.supervisor import tally_tool_outcomes
from stackowl.pipeline.persistence import is_structural_giveup

def test_tally_reads_failed_bool_not_marker():
    # The marker is STRIPPED from result before storage; only the typed bool is authoritative.
    calls = [
        {"name": "browser_browse", "result": "host unknown", "failed": True},
        {"name": "shell", "result": "ok output", "failed": False},
    ]
    failures, successes = tally_tool_outcomes(calls)
    assert failures == 1
    assert successes == 1

def test_tally_ignores_marker_in_result_string():
    # Even if a result coincidentally contains marker-like text, only "failed" counts.
    calls = [{"name": "x", "result": "\x00TOOL_FAILED\x00 leftover", "failed": False}]
    failures, successes = tally_tool_outcomes(calls)
    assert failures == 0 and successes == 1

def test_structural_giveup_true_on_failed_and_trivial_draft():
    assert is_structural_giveup(tool_failures=1, successful_tool_calls=0, draft="...") is True

def test_structural_giveup_false_on_substantive_draft():
    # Knowledge-answer / negative-result after an incidental failure is NOT a give-up.
    assert is_structural_giveup(tool_failures=1, successful_tool_calls=0,
                                draft="No, the file does not exist.") is False

def test_structural_giveup_false_when_a_tool_succeeded():
    assert is_structural_giveup(tool_failures=1, successful_tool_calls=1, draft="...") is False
```

- [ ] **Step 2: Run to verify fail** — `uv run pytest tests/pipeline/test_supervisor_tally.py -v` → ImportError.

- [ ] **Step 3: Implement**

```python
# src/stackowl/pipeline/supervisor.py  (new file — header + tally)
"""Self-healing turn supervisor: detection veto, never-empty floor, shared tally."""
from __future__ import annotations
from stackowl.logger import log

def tally_tool_outcomes(all_calls: list[dict[str, object]]) -> tuple[int, int]:
    """Count failed/successful tool calls from the AUTHORITATIVE typed `failed` bool.

    NEVER re-scan call["result"] for TOOL_FAILED_MARKER — the marker is stripped
    before the result is stored (anthropic_provider.py:286 / openai_provider.py),
    so a re-scan is always False and the structural net would silently never fire.
    """
    failures = sum(1 for c in all_calls if bool(c.get("failed")))
    successes = sum(1 for c in all_calls if not bool(c.get("failed")))
    log.engine.debug("supervisor.tally", extra={"_fields": {"failures": failures, "successes": successes}})
    return failures, successes
```

```python
# src/stackowl/pipeline/persistence.py  — add near _structurally_irrelevant (~line 108)
def is_structural_giveup(*, tool_failures: int, successful_tool_calls: int, draft: str) -> bool:
    """Structural give-up signal — language-agnostic, no model call.

    True only for the genuine zombie shape: at least one tool failed, NOTHING
    succeeded, AND the draft is trivial/refusing (a substantive knowledge-answer
    or negative-result draft is NOT a give-up). Gates out the false-positive class.
    """
    return (
        tool_failures >= 1
        and successful_tool_calls == 0
        and _structurally_irrelevant(draft)
    )
```

- [ ] **Step 4: Run to verify pass** — `uv run pytest tests/pipeline/test_supervisor_tally.py -v` → 5 pass.

- [ ] **Step 5: Commit**

```bash
cd /ssd/projects/stackowl-personal-ai-assistant && git add v2/src/stackowl/pipeline/supervisor.py v2/src/stackowl/pipeline/persistence.py v2/tests/pipeline/test_supervisor_tally.py && git commit -m "feat(v2): supervisor tally + structural give-up signal (self-heal W1.T1)" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

### Task 2: Structural veto + escalation-reward nudge budget

**Files:**
- Modify: `src/stackowl/pipeline/supervisor.py`
- Test: `tests/pipeline/test_supervisor_veto.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/pipeline/test_supervisor_veto.py
from stackowl.pipeline.supervisor import apply_structural_veto
from stackowl.pipeline.persistence import PERSISTENCE_DIRECTIVE

LYING_CALLS = [{"name": "browser_browse", "failed": True}]  # tool failed, nothing succeeded

def test_veto_overrides_lying_delivered_on_giveup():
    # Judge said DELIVERED (None directive) but structurally it's a give-up + trivial draft.
    out = apply_structural_veto(judge_directive=None, all_calls=LYING_CALLS, draft="...")
    assert out == PERSISTENCE_DIRECTIVE  # veto fires

def test_no_veto_when_draft_substantive():
    out = apply_structural_veto(judge_directive=None, all_calls=LYING_CALLS,
                                draft="The capital of France is Paris.")
    assert out is None  # substantive answer -> not a give-up

def test_no_veto_when_a_tool_succeeded():
    calls = [{"name": "x", "failed": True}, {"name": "y", "failed": False}]
    assert apply_structural_veto(judge_directive=None, all_calls=calls, draft="...") is None

def test_judge_directive_passes_through_when_set():
    # If the judge itself flagged a give-up, keep its directive (no double-injection).
    out = apply_structural_veto(judge_directive=PERSISTENCE_DIRECTIVE, all_calls=LYING_CALLS, draft="...")
    assert out == PERSISTENCE_DIRECTIVE
```

- [ ] **Step 2: Run to verify fail** — ImportError.

- [ ] **Step 3: Implement**

```python
# src/stackowl/pipeline/supervisor.py  — append
from stackowl.pipeline.persistence import PERSISTENCE_DIRECTIVE, is_structural_giveup

def apply_structural_veto(
    *, judge_directive: str | None, all_calls: list[dict[str, object]], draft: str
) -> str | None:
    """Always-on structural veto over the judge's verdict.

    If the judge returned a directive (it already flagged a give-up), keep it.
    Otherwise compute the structural signal from the AUTHORITATIVE `failed` bools;
    if it's a give-up, OVERRIDE the judge's (possibly hallucinated) DELIVERED and
    inject the persistence directive. This catches a weak local judge that returns
    a confident-but-wrong "delivered" — the actual Jetson failure mode.
    """
    if judge_directive is not None:
        return judge_directive
    failures, successes = tally_tool_outcomes(all_calls)
    if is_structural_giveup(tool_failures=failures, successful_tool_calls=successes, draft=draft):
        log.engine.debug("supervisor.veto: overriding judge DELIVERED on structural give-up")
        return PERSISTENCE_DIRECTIVE
    return None
```

> **Note on the escalation-reward cap:** the budget decrement stays in each provider's `_enforce` (Task 4/5), where the budget var lives. The rule "only decrement when the nudge produced no new tool call" is implemented there because it needs the per-iteration loop state.

- [ ] **Step 4: Run to verify pass** — 4 pass.

- [ ] **Step 5: Commit**

```bash
cd /ssd/projects/stackowl-personal-ai-assistant && git add v2/src/stackowl/pipeline/supervisor.py v2/tests/pipeline/test_supervisor_veto.py && git commit -m "feat(v2): always-on structural veto over a lying judge (self-heal W1.T2)" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

### Task 3: Fallback judge tier + drop the interactive/depth gate

**Files:**
- Modify: `src/stackowl/pipeline/steps/execute.py` (`_persistence_check` ~452–487)
- Test: `tests/pipeline/test_persistence_check_fallback.py`

- [ ] **Step 1: Write the failing test** — drive `_persistence_check` (extract it to a module-level helper `build_persistence_check(state, get_services)` if it is a nested closure, so it is testable). The test: a primary judge provider that RAISES → the fallback local provider is consulted; if the fallback rules give-up → directive returned (not None). And a delegated turn (`interactive=False`) now still gets a non-None checker (gate dropped).

```python
# tests/pipeline/test_persistence_check_fallback.py
import pytest
from stackowl.pipeline.steps.execute import build_persistence_check

class _RaisingProvider:
    async def complete(self, *a, **k): raise RuntimeError("judge down")

class _FallbackProvider:
    async def complete(self, *a, **k): return '{"delivered": false, "reason": "stub"}'

@pytest.mark.asyncio
async def test_fallback_judge_used_when_primary_raises(monkeypatch, fake_state, fake_services):
    # primary raises -> fallback consulted -> returns a give-up directive
    check = build_persistence_check(fake_state, fake_services,
                                    primary=_RaisingProvider(), fallback=_FallbackProvider())
    directive = await check("a give-up draft", ["browser_browse(failed)"])
    assert directive is not None

@pytest.mark.asyncio
async def test_checker_present_for_delegated_turn(fake_state_delegated, fake_services):
    # gate dropped: non-interactive / depth>0 still gets a checker
    check = build_persistence_check(fake_state_delegated, fake_services, primary=_FallbackProvider())
    assert check is not None
```

- [ ] **Step 2: Run to verify fail.**

- [ ] **Step 3: Implement** — refactor the nested `_persistence_check` into a module-level `build_persistence_check(state, services, *, primary=None, fallback=None)` returning an async callable. Primary judge via `preg.get_with_cascade("fast")`; on exception, retry on the local always-available provider (`preg.get_with_cascade("local")` or the configured local tier — confirm the registry key in recon-of-recon; use the existing local-provider accessor). Each `except` logs. Remove the `if state.interactive and state.delegation_depth == 0:` gate so the checker is always built. Keep the existing `judge_delivery` rubric call. Return `PERSISTENCE_DIRECTIVE` on a give-up verdict, else `None`.

- [ ] **Step 4: Run to verify pass.**

- [ ] **Step 5: Commit** (`feat(v2): persistence-check fallback judge tier + all-turns enforcement (self-heal W1.T3)`).

### Task 4: Wire veto into anthropic `_enforce` + escalation-reward cap

**Files:**
- Modify: `src/stackowl/providers/anthropic_provider.py` (`_enforce` ~177–200)
- Test: `tests/providers/test_anthropic_enforce_veto.py`

- [ ] **Step 1: Write the failing test** — a fake persistence_check that returns `None` (judge says delivered), `all_calls` showing a failed tool + trivial draft → `_enforce` returns the directive (veto). And: after a nudge, if the next iteration made a NEW tool call, the budget is NOT decremented (escalation reward); if it re-refused, it IS.

- [ ] **Step 2: Run to verify fail.**

- [ ] **Step 3: Implement** — inside `_enforce`, after obtaining `judge_directive` from `persistence_check` (keeping the fail-open try/except but the structural veto now runs REGARDLESS): `directive = apply_structural_veto(judge_directive=judge_directive, all_calls=all_calls, draft=content)`. Budget: track `tool_calls_before_nudge`; decrement `nudge_budget` only if the post-nudge iteration produced no new `all_calls` entry. Every `except` logs.

- [ ] **Step 4: Run to verify pass.**

- [ ] **Step 5: Commit** (`feat(v2): anthropic _enforce structural veto + escalation-reward cap (self-heal W1.T4)`).

### Task 5: Wire the same into openai `_enforce` (DRY)

**Files:**
- Modify: `src/stackowl/providers/openai_provider.py` (`_enforce` ~197–225)
- Test: `tests/providers/test_openai_enforce_veto.py`

- [ ] **Step 1–4:** mirror Task 4 against the openai provider, calling the SAME `apply_structural_veto` helper (no duplicated logic — only the wiring differs). Same escalation-reward cap.
- [ ] **Step 5: Commit** (`feat(v2): openai _enforce structural veto (self-heal W1.T5)`).

### Task 6: Gateway merge-gate — the lying-judge test

**Files:**
- Test: `tests/journeys/test_self_heal_lying_judge.py`

- [ ] **Step 1: Write the test** — mirror `tests/journeys/test_j1_*` infra. Scripted provider: iter 0 calls a tool that returns `TOOL_FAILED` (simulated DNS); iter 1 emits a polished give-up draft with no successful tool call. The JUDGE provider is scripted to return valid `{"delivered": true}` (alive and WRONG). Assert the OUTCOME: the structural veto fires → a re-route is attempted → the final user response is **non-empty** and **names the blocked capability**. This is the headline gate — it fails if the veto hole is open.
- [ ] **Step 2: Run to verify fail** (before W1 fully wired) / **pass** (after).
- [ ] **Step 5: Commit** (`test(v2): lying-judge merge gate — veto kills the zombie (self-heal W1.T6)`).

---

# Workstream 2 — Never-empty floor (TerminalResponseGuarantee + invariant)

### Task 7: Localization slot support + floor catalog keys

**Files:**
- Modify: `src/stackowl/setup/localize.py`
- Test: `tests/setup/test_localize_format.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/setup/test_localize_format.py
from stackowl.setup.localize import localize_format

def test_localize_format_fills_slots():
    out = localize_format("self_heal_floor", "en",
                          goal="browse a site", failed_capability="browser_browse",
                          attempts="browser_browse", partial="", error="NS_ERROR_UNKNOWN_HOST")
    assert "browser_browse" in out and "NS_ERROR_UNKNOWN_HOST" in out
    assert out  # non-empty

def test_localize_format_missing_slot_does_not_raise():
    # Resilient: a missing slot leaves a readable string, never a KeyError.
    out = localize_format("self_heal_floor", "en", goal="x")
    assert out
```

- [ ] **Step 2: Run to verify fail.**

- [ ] **Step 3: Implement** — add `localize_format(key, lang="en", **slots) -> str` that calls `localize(key, lang)` then `.format_map(_SafeDict(slots))` where `_SafeDict` returns `""` (or the bare `{name}`) for missing keys so it never raises. Add catalog entries `("self_heal_floor", "en")` (and at least one other language, e.g. `"de"`, to prove multilingual) with the template, e.g.:
`"I couldn't fully complete this: {goal}. The capability that failed: {failed_capability}. What I tried: {attempts}. {partial} Technical detail: {error}"`. Keep it neutral/global.

- [ ] **Step 4: Run to verify pass.**
- [ ] **Step 5: Commit** (`feat(v2): localize_format slot support + self-heal floor catalog (self-heal W2.T7)`).

### Task 8: TerminalResponseGuarantee (pure floor, two entry points)

**Files:**
- Modify: `src/stackowl/pipeline/supervisor.py`
- Test: `tests/pipeline/test_terminal_floor.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/pipeline/test_terminal_floor.py
from stackowl.pipeline.supervisor import synthesize_from_calls, synthesize_floor

def test_synthesize_from_calls_non_empty_and_honest():
    calls = [{"name": "browser_browse", "failed": True, "result": "NS_ERROR_UNKNOWN_HOST"}]
    out = synthesize_from_calls(goal="open example.com", all_calls=calls, partial="")
    assert out and "browser_browse" in out

def test_synthesize_floor_degraded_when_no_calls():
    # Hard-exception path: tool records lost — still non-empty, uses goal+error+partial.
    out = synthesize_floor(goal="open example.com", error="boom", attempts=[], partial="prior text")
    assert out

def test_floor_never_raises_on_garbage():
    out = synthesize_floor(goal=None, error=None, attempts=None, partial=None)  # type: ignore[arg-type]
    assert out  # minimal localized non-empty string, no exception
```

- [ ] **Step 2: Run to verify fail.**

- [ ] **Step 3: Implement** — `synthesize_from_calls(goal, all_calls, partial)` derives `failed_capability` (first `c["name"]` where `failed`), `attempts` (all names), `error` (the failed call's truncated `result`), then `synthesize_floor(...)`. `synthesize_floor(goal, error, attempts, partial)` calls `localize_format("self_heal_floor", lang, ...)` wrapped in a try/except that, on ANY error, returns `localize("self_heal_floor_minimal", lang)` (a static non-empty fallback key) — the floor NEVER raises and NEVER returns empty. Lang resolution from a passed `lang="en"` default (thread the turn lang where available; default "en").

- [ ] **Step 4: Run to verify pass.**
- [ ] **Step 5: Commit** (`feat(v2): TerminalResponseGuarantee pure never-empty floor (self-heal W2.T8)`).

### Task 9: Floor into provider empty wrap-up (both providers)

**Files:**
- Modify: `src/stackowl/providers/anthropic_provider.py` (~355), `src/stackowl/providers/openai_provider.py` (~385)
- Test: `tests/providers/test_provider_empty_wrapup_floor.py`

- [ ] **Step 1: Write the failing test** — force the wrap-up path to produce empty text (scripted) → the provider returns a non-empty floored string (via `synthesize_from_calls(user_text, all_calls, _last_assistant_text(messages))`) instead of `""`. Both providers.
- [ ] **Step 2: fail.**
- [ ] **Step 3: Implement** — replace `return "", all_calls` with `return synthesize_from_calls(user_text, all_calls, _last_assistant_text(messages)) or "", all_calls` — but assert it's never empty (the floor guarantees non-empty). Build `{partial}` from pre-wrap-up state, NOT post-`WRAPUP_DIRECTIVE` messages (avoid echoing the directive). Log at warning that the floor fired.
- [ ] **Step 4: pass.**
- [ ] **Step 5: Commit** (`fix(v2): provider empty wrap-up returns honest floor not "" (self-heal W2.T9)`).

### Task 10: Floor into execute.py hard-exception/empty-final + attach all_calls to exceptions (INVARIANT)

**Files:**
- Modify: `src/stackowl/pipeline/steps/execute.py` (bare-except ~778–784, normal-exit guard ~798), and the providers to attach partial `all_calls` to the bare exception (mirror `TurnStopped` carrying `tool_call_records`)
- Test: `tests/pipeline/test_execute_floor_invariant.py`

- [ ] **Step 1: Write the failing test** — the LOAD-BEARING invariant test:

```python
# tests/pipeline/test_execute_floor_invariant.py
@pytest.mark.asyncio
async def test_hard_exception_floors_response_but_keeps_errors(fake_state, raising_provider):
    # provider raises mid-loop -> floor adds a non-empty response, errors STAYS non-empty.
    out_state = await execute_step.run(fake_state)  # adapt to the real entrypoint
    assert out_state.responses and out_state.responses[-1].text  # non-empty user-facing
    assert out_state.errors  # INVARIANT: failure still recorded -> durable status stays failed
    assert out_state.durable_parked is False or out_state.errors  # status not faked to success
```

- [ ] **Step 2: fail.**

- [ ] **Step 3: Implement** — in the bare `except Exception` (~778), build `floor = synthesize_floor(goal=state.input_text, error=str(exc), attempts=_attempts_from(exc), partial=_last_response_text(state))` and return `state.evolve(responses=(*state.responses, ResponseChunk(text=floor, target=state.reply_target)), errors=(*state.errors, marker))` — **responses AND errors both set; never clear errors**. At the normal-exit `if final_text:` guard (~798), add an `else:` that floors from `state` so an empty `final_text` never yields no chunk. Attach `all_calls` to the exception in the provider (a new attribute on a wrapping exception, or pass partial records through) so `_attempts_from(exc)` is populated; if absent, the floor degrades gracefully (goal+error+partial). Every `except` logs.

- [ ] **Step 4: pass.**
- [ ] **Step 5: Commit** (`fix(v2): never-empty floor at hard-exception/empty-final, responses-only invariant (self-heal W2.T10)`).

### Task 11: Gateway responses-only-invariant test

**Files:** Test: `tests/journeys/test_self_heal_invariant.py`

- [ ] **Step 1:** gateway test — a hard provider exception mid-turn → assert the user gets a non-empty honest message AND (durable variant) the task terminal status is still `failed` (`errors` non-empty), AND a delegated variant returns `A2AResult.status != ok`. Asserts the STRUCTURED outcome, never merely "non-empty".
- [ ] **Step 5: Commit** (`test(v2): responses-only invariant gateway gate (self-heal W2.T11)`).

---

# Workstream 3 — Recovery actuator (capability substitution)

### Task 12: `capability_tag` on ToolManifest + tag the web_knowledge class

**Files:**
- Modify: `src/stackowl/tools/base.py` (`ToolManifest`), `tools/browser/browse.py`, `tools/search/web_search.py`, `tools/io/web_fetch.py`
- Test: `tests/tools/test_capability_tag.py`

- [ ] **Step 1: Write the failing test** — `assert browse_tool.manifest.capability_tag == "web_knowledge"` for all three; default `None` for an untagged tool.
- [ ] **Step 2: fail.**
- [ ] **Step 3: Implement** — add `capability_tag: str | None = None` to `ToolManifest` (frozen Pydantic, additive). Set `capability_tag="web_knowledge"` on the three manifests. No other behavior change.
- [ ] **Step 4: pass.**
- [ ] **Step 5: Commit** (`feat(v2): capability_tag manifest field + tag web_knowledge tools (self-heal W3.T12)`).

### Task 13: Substitution-class registry + normalized-input adapters

**Files:**
- Create: `src/stackowl/pipeline/capability_substitution.py`
- Test: `tests/pipeline/test_capability_substitution.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/pipeline/test_capability_substitution.py
from stackowl.pipeline.capability_substitution import normalized_input_for, build_args_for

def test_normalized_input_from_failed_browse():
    failed_args = {"task": "find the weather", "seed_url": "https://x.example"}
    ni = normalized_input_for("browser_browse", failed_args)
    assert ni == {"url": "https://x.example", "query": "find the weather"}

def test_build_args_for_web_fetch_uses_url():
    assert build_args_for("web_fetch", {"url": "https://x.example", "query": "q"}) == {"url": "https://x.example"}

def test_build_args_for_web_search_uses_query():
    assert build_args_for("web_search", {"url": "https://x.example", "query": "q"}) == {"query": "q"}

def test_build_args_returns_none_when_unservable():
    # web_fetch needs a url; none available -> cannot serve.
    assert build_args_for("web_fetch", {"query": "q"}) is None
```

- [ ] **Step 2: fail.**
- [ ] **Step 3: Implement** — a declarative `_ADAPTERS: dict[str, ...]` mapping each tool name to (a) how to produce the normalized input from ITS failed args, and (b) how to build ITS args from a normalized input (returning `None` when it can't serve). Keep it data-driven and small; the supervisor stays generic. `find_substitute(failed_tool, failed_args, registry, in_bounds_fn) -> (tool_name, args) | None` (added in Task 14's test scope) picks the highest-priority in-bounds, non-consequential sibling sharing the `capability_tag`.
- [ ] **Step 4: pass.**
- [ ] **Step 5: Commit** (`feat(v2): capability-substitution class registry + adapters (self-heal W3.T13)`).

### Task 14: Deterministic substitution at the dispatch seam

**Files:**
- Modify: `src/stackowl/pipeline/steps/execute.py` (`_dispatch` ~416–444), `src/stackowl/pipeline/capability_substitution.py` (`find_substitute`)
- Test: `tests/pipeline/test_dispatch_substitution.py`

- [ ] **Step 1: Write the failing test** — in `_dispatch`, when tool X (with a `capability_tag`) returns `TOOL_FAILED`, the dispatcher finds an in-bounds, **non-consequential** sibling sharing the tag, executes it with adapter-built args, and returns the sibling's SUCCESS output (a fresh observation), logging the substitution. Bounded: one substitution per failed capability per turn. A consequential sibling is NOT auto-run. Out-of-bounds sibling skipped.
- [ ] **Step 2: fail.**
- [ ] **Step 3: Implement** — after the failure return at ~444, before returning the marker: call `find_substitute(name, args, tool_registry, lambda n: check_effective_bounds(effective, n) is None)` filtered to `manifest.action_severity in ("read","write")` (NOT "consequential"); if found and not already substituted this turn (track a per-turn set on `state`/a dispatch-scoped flag), `await ledger_guard(...)` the sibling and, on success, return its output as the observation (prefixed with a neutral note that an alternative was used — localized, no English literal). On sibling failure, fall through to the original `TOOL_FAILED` marker. Charge against the same budget/slots. Every `except` logs.
- [ ] **Step 4: pass.**
- [ ] **Step 5: Commit** (`feat(v2): deterministic capability substitution at dispatch seam (self-heal W3.T14)`).

### Task 15: Gateway substitution + consent-safety tests

**Files:** Test: `tests/journeys/test_self_heal_substitution.py`

- [ ] **Step 1:** (a) `browser_browse` (consequential) fails → `web_fetch`/`web_search` (read) auto-runs → the user gets a real answer derived from the sibling (route-around proven end-to-end). (b) Consent-safety: a failed tool whose only sibling is consequential → the sibling is NOT auto-executed (no consent prompt bypassed); instead the named alternative is surfaced. Assert the OUTCOME.
- [ ] **Step 5: Commit** (`test(v2): substitution route-around + consent-safety gates (self-heal W3.T15)`).

---

# Workstream 4 — Enforcement surface

### Task 16: Delegation + parliament honesty (floor feeds honest-failure builders)

**Files:**
- Modify: `src/stackowl/tools/agents/results.py`
- Test: `tests/tools/agents/test_delegation_floor_status.py`

- [ ] **Step 1: Write the failing test** — a delegated child turn that ends via the floor → the parent receives `_honest_failed_result(... msg=<floor text>)` with `success=False` and a failure `status` in the record; assert the floor text rides in the `result`/`msg` but `status != "ok"` (the builder owns status; the floor never sets it).
- [ ] **Step 2: fail.**
- [ ] **Step 3: Implement** — where a delegated turn's terminal text is built, route a floored failure through `_honest_failed_result` (status owned there), not as a bare `ok` text. Parliament synthesis: ensure a floored sub-owl failure is carried as structured status so synthesis can skip it. Minimal change.
- [ ] **Step 4: pass.**
- [ ] **Step 5: Commit** (`fix(v2): delegated give-up floors into honest-failure status, not fake ok (self-heal W4.T16)`).

### Task 17: Steer pre-emption + budget charging + no-run-on-terminal-exits

**Files:**
- Modify: `src/stackowl/providers/anthropic_provider.py` + `openai_provider.py` (the iteration-callback composition / `_enforce` guard)
- Test: `tests/providers/test_enforce_exit_safety.py`

- [ ] **Step 1: Write the failing test** — (a) a pending live-steer message at the same iteration boundary PRE-EMPTS a give-up nudge (steer wins); (b) DeliveryGuard does NOT run when the exit is `TurnStopped`/`BudgetBreach`; (c) a nudge is charged against the budget governor (a budget-exhausted turn does not nudge).
- [ ] **Step 2: fail.**
- [ ] **Step 3: Implement** — in the callback composition, order steer-handling before give-up nudging; guard `_enforce` to skip when a stop/breach is in flight; consult the budget governor before injecting a nudge. Minimal, every `except` logs.
- [ ] **Step 4: pass.**
- [ ] **Step 5: Commit** (`fix(v2): steer pre-empts give-up nudge; budget-charged; skip on stop/breach (self-heal W4.T17)`).

### Task 18: Idempotency-triad false-positive guards

**Files:** Test: `tests/journeys/test_self_heal_false_positives.py`

- [ ] **Step 1:** three gateway tests, each asserting the final answer is UNCHANGED after any spurious nudge: (a) knowledge-answer-after-failed-search ("capital of France" → search fails → "Paris" stands); (b) file-not-found-is-the-answer ("does X exist?" → not-found → "No" stands); (c) steer-abandoned-call (user steers mid-turn, the abandoned tool call's failure does not trigger a re-route of the OLD goal). These prove the net does not corrupt correct answers.
- [ ] **Step 5: Commit** (`test(v2): idempotency-triad false-positive guards (self-heal W4.T18)`).

---

# Workstream 5 — Bug fixes

### Task 19: browser_browse goto guard + structured classification

**Files:**
- Modify: `src/stackowl/tools/browser/browse.py` (seed ~228, inner ~383, timeout ~24)
- Test: `tests/tools/browser/test_browse_nav_errors.py`

- [ ] **Step 1: Write the failing test** — a fake page whose `goto` raises a Playwright error with `NS_ERROR_UNKNOWN_HOST` → `execute` does NOT propagate it as an unhandled exception; instead the inner loop records a structured handled-failure observation classifying it as unknown-host (by the stable error code, not English prose), and the tool returns a failed-but-clean `ToolResult` (no "unhandled exception" log). A timeout code → classified as timeout. Confirm the nav timeout bounds the call.
- [ ] **Step 2: fail.**
- [ ] **Step 3: Implement** — wrap both `page.goto` calls in `try/except` catching the Playwright error; classify by matching the stable error-code substring (`NS_ERROR_UNKNOWN_HOST`, timeout markers, connection-reset markers — identifiers) into a small enum/string; feed a structured observation into the inner loop (mirror the existing recovery-path handling at ~264). Log the classified failure at warning (not error). Verify `acquire_domain_slot` releases on the failure path so it can't hang (the 6-min duration); add a release in `finally` if missing. Every `except` logs.
- [ ] **Step 4: pass.**
- [ ] **Step 5: Commit** (`fix(v2): browser_browse goto guard + structured nav-error classification (self-heal W5.T19)`).

### Task 20: memory audit INSERT datatype-mismatch fix

**Files:**
- Modify: `src/stackowl/memory/dream_worker_helpers.py` (`_INSERT_AUDIT_SQL` ~116, `mark_audit_contradictions` ~360–402)
- Test: `tests/memory/test_audit_contradiction_insert.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/memory/test_audit_contradiction_insert.py
@pytest.mark.asyncio
async def test_mark_audit_contradictions_row_lands(tmp_db_pool):
    # Run the contradiction-marking; assert a row ACTUALLY lands in audit_log (no datatype mismatch).
    await mark_audit_contradictions(tmp_db_pool, reports=[_fake_report("fa", "fb")])
    rows = await tmp_db_pool.fetchall("SELECT audit_id, timestamp, event_type FROM audit_log")
    assert len(rows) == 1
    assert isinstance(rows[0]["audit_id"], int)        # AUTOINCREMENT integer, not a UUID string
    assert isinstance(rows[0]["timestamp"], float)     # REAL, not an ISO string
```

- [ ] **Step 2: Run to verify fail** — `sqlite3.IntegrityError: datatype mismatch`.
- [ ] **Step 3: Implement** — change `_INSERT_AUDIT_SQL` to the canonical 5-col form (omit `audit_id`; include `integrity_hash` to match `audit/logger.py` if writing the hash, else the 5-col `scheduler_helpers` form). In `mark_audit_contradictions`, drop the `str(uuid.uuid4())` audit_id bind and bind `time.time()` (float) for `timestamp`. `import time`. Mirror `scheduler_helpers.write_audit` exactly. No migration. Keep the WARNING-on-failure swallow (best-effort) but the write now succeeds.
- [ ] **Step 4: Run to verify pass.**
- [ ] **Step 5: Commit** (`fix(v2): mark_audit_contradictions binds no audit_id + float ts — kills datatype mismatch (self-heal W5.T20)`).

---

## Final review + merge

- [ ] After all 20 tasks: dispatch a holistic code-review subagent over the whole diff (focus: the load-bearing responses-only invariant holds at EVERY exit; the veto + substitution don't break TurnStopped/BudgetBreach/durable replay; DRY across the two providers; no hardcoded English; consent/bounds intact on substitution).
- [ ] Run the targeted suites: `uv run pytest tests/pipeline tests/providers tests/tools/browser tests/tools/search tests/memory tests/journeys/test_self_heal_*.py tests/tools/agents -q`.
- [ ] `uv run ruff check src/` + `uv run mypy src/stackowl/pipeline src/stackowl/providers src/stackowl/tools/base.py`.
- [ ] Use finishing-a-development-branch → merge to main + push (per the always-merge rule, clean tree first).

## Self-review (plan vs spec)

- **Spec coverage:** DeliveryGuard cascade+veto (T1–T6); never-empty floor + invariant (T7–T11); capability substitution (T12–T15); enforcement surface incl. delegation/parliament/steer/budget (T16–T18); bug fixes (T19–T20). The lying-judge headline gate (T6), idempotency triad (T18), and responses-only invariant (T10/T11) are explicit. ✓
- **Type consistency:** `tally_tool_outcomes`, `is_structural_giveup`, `apply_structural_veto`, `synthesize_from_calls`/`synthesize_floor`, `capability_tag`, `find_substitute`/`normalized_input_for`/`build_args_for`, `build_persistence_check` — names used consistently across tasks. ✓
- **Known recon-to-pin during execution:** the local-fallback provider registry key (T3), the exact exception-payload attachment for `all_calls` (T10), and the iteration-callback composition point for steer-preemption (T17) — each task says to confirm against the live code before implementing; not placeholders, but the first step of those tasks.
