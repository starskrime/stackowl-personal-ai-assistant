# Verification Primitive — Design Spec

**Date:** 2026-06-25
**Branch:** `feat/verification-primitive`
**Status:** approved-design (pre-implementation)

## 1. The root cause this fixes

StackOwl has no objective **verification** primitive. Every "success" signal is
either *asserted* (a self-reported boolean — often a process exit code) or
*guessed* (an LLM judge reading draft text) — never *measured against reality*.
Because the foundational bit `ToolResult.success` is unverified at its source,
every layer built on it (the delivery judge, the honesty floors, the overclaim
gate, the learning loop, the objective planner) reasons on a signal that can be a
lie.

Smoking-gun anchors:
- `tools/system/shell.py:486` — `success = proc.returncode == 0` (the
  `yt-dlp --simulate --no-download` bug: did nothing, exit 0, "succeeded").
- `tools/io/write_file.py:86` — returns `success=True` after `write_text` without
  reading the file back.
- `tools/media/image_generate.py:196`, `tools/media/tts.py:193` — `success=True`
  on a backend's word; the artifact is never stat'd.
- `pipeline/backends/asyncio_backend.py:256` — turn `success = len(errors) == 0`
  ("didn't crash" ≠ "accomplished the task").
- `objectives/driver.py:163` — a sub-goal is "done" when no error was thrown; no
  acceptance criterion on `Subgoal` (`objectives/model.py:52`).
- `pipeline/persistence.py:366` — the delivery judge reads request + draft text +
  tool self-reports, never the filesystem; fails OPEN.
- `pipeline/state.py` — `PipelineState` has no goal / acceptance-criteria /
  expected-artifact field.

**Principle: verification > representation.** The cheapest, highest-leverage fix
is a post-condition check *at the tool boundary* — "a tool that claims an effect
must observe that effect before reporting it." We then lift the same idea to a
per-turn / per-goal acceptance check owned by **one** authority, so the disjoint
honesty-proxy pile can begin to collapse.

## 2. Decisions (owner-settled 2026-06-25)

| Fork | Decision |
|------|----------|
| Mechanism | **`verify()` hook on the `Tool` ABC, with `verify_artifact()` helper as the declarative shorthand.** The hook is the primitive; the helper is the common-case spec. |
| Failure semantics | **Add a separate `verified` field**, do NOT mutate `success`. Preserve the distinction "what the tool claimed" vs. "what reality confirmed." |
| Scope | **Full agentic solution this branch** — Phase 1 (tool boundary) AND Phase 2 (goal-level acceptance authority), fully wired. |
| Phase-2 acceptance source | **Both, behavior- and result-gated**: a deterministic per-tool `verified` ledger always on, PLUS an LLM-derived expected-outcome reality-check that engages only when the turn's *behavior* (an effectful tool ran) and *result* (the draft claims an outcome) indicate an effect was promised. |
| Default state | **ON by default** on the live platform. Fail-safe: `verified=None` always falls back to `success`; only an explicit reality-disagreement flips trust. |

## 3. The contract

### 3.1 `ToolResult.verified: bool | None` (tri-state, additive)

```python
class ToolResult(BaseModel):
    success: bool                       # unchanged: the tool's self-report
    verified: bool | None = None        # NEW: reality check
    ...
```

- `None` → no post-condition ran (un-migrated tool, or verification could not be
  performed). **Byte-identical to today.**
- `True` → the claimed effect was observed in reality.
- `False` → the tool claimed `success=True` but reality disagreed
  (the absent/empty artifact case).

`success` semantics are untouched — no lie-laundering, no mutation.

### 3.2 One derived predicate, read everywhere

```python
def is_trustworthy_success(success: bool, verified: bool | None) -> bool:
    return success and verified is not False
```

This single function (and its ledger-level twin) is what the deciders consume:
- `verified is False` → not a trustworthy success → the floor fires, the learning
  miner skips it, the judge sees a failed tool, turn-success goes false.
- `verified is None` → falls back to `success` → today's behavior, exactly.

### 3.3 The `verify()` hook + `verify_artifact()` helper

On the `Tool` ABC (`tools/base.py`):

```python
async def verify(self, args: dict, result: ToolResult) -> bool | None:
    """Observe reality to confirm the claimed effect. Default: no verification.

    Return True (effect observed), False (claimed but absent), or None
    (not applicable / could not check). Runs ONLY after a success=True execute.
    Must never raise (caller wraps); never re-do the side effect.
    """
    return None
```

Shared helper (`tools/verification.py`, new):

```python
def verify_artifact(path: str | Path | None) -> bool | None:
    """True iff `path` exists and is a non-empty regular file.
    None when path is None/empty (nothing claimed). Never raises."""
```

`Tool.__call__` (the single universal dispatch seam) stamps the bit:

```python
async def __call__(self, **kwargs):
    result = await self.execute(**kwargs)        # unchanged
    if result.success and result.verified is None:
        try:
            verdict = await self.verify(kwargs, result)
        except Exception:
            verdict = None                        # fail-safe: never block on verify
        if verdict is not None:
            result = result.model_copy(update={"verified": verdict})
    return result
```

A tool that does not override `verify()` returns `None` → result unchanged → the
56 + 36 un-migrated tools are byte-identical.

## 4. Phase 1 — tool-boundary verification

### 4.1 Migration set (tools that name their own artifact)

| Tool | Post-condition |
|------|----------------|
| `write_file` | `verify_artifact(target)` — the written path exists & non-empty |
| `edit` | edited file exists & contains the applied change (artifact exists, non-empty) |
| `apply_patch` | each touched path exists post-apply (artifact exists) |
| `pdf` (write paths) | output path exists & non-empty |
| `image_generate` | `verify_artifact(result.path)` |
| `tts` | `verify_artifact(result.path)` |
| `get_images` | downloaded file(s) exist & non-empty |
| `send_file` | precondition (input file exists/non-empty) already checked at
  send_file.py:278; coupling is `unconfirmed` (lossy channel boundary), so
  `verify()` confirms the *local* artifact it claims to send, not delivery |

`shell` is intentionally **excluded** from Phase-1 self-verification: it is
open-ended and cannot know what artifact a bare command "should" have produced.
That class is caught by Phase 2.

### 4.2 Wiring the deciders (the ledger is the backbone)

`infra/tool_outcome_ledger.py` is already the single source of truth the floor,
the execute snapshot, and the tally all read. We thread `verified` through it:

- `ToolOutcome` gains `verified: bool | None = None`.
- `execute.py:1053` `record_tool_outcome(...)` passes `verified=tr.verified`.
- `is_effectful_failure(severity, success, side_effect_committed, verified=None)`
  returns True also when `severity in EFFECTFUL and side_effect_committed and
  verified is False` — i.e. a *claimed-but-unverified* write/consequential is an
  effectful failure. `consequential_tally` counts a `verified=False` as a failure,
  not a success.

This makes the entire honesty pile (giveup floor, overclaim gate, snapshot)
verification-aware in **one** edit, with no per-floor changes.

- Turn-success at `asyncio_backend.py:256` and the `task_outcomes` capture record
  the trustworthy bit (a new `verified`-derived column / failure_class), so the
  reflection trigger and `tool_outcome_miner` (positive-only) never mine a
  `verified=False` false win.

## 5. Phase 2 — goal-level acceptance authority

### 5.1 Representation

- `PipelineState` gains `expected_outcome: ExpectedOutcome | None = None`
  (additive; default None = byte-identical). `ExpectedOutcome` is a small frozen
  model: `{ kind, artifact_dir?, description }`.
- `Subgoal` (`objectives/model.py`) gains `acceptance_criteria: str | None = None`
  and is checked at done-decision time.

### 5.2 The single authority: `AcceptanceChecker`

`pipeline/acceptance.py` (new) — the ONE place a turn/goal's outcome is judged
against reality. It is **behavior- and result-gated**:

1. **Behavior gate** — engages only if an effectful (write/consequential) tool ran
   this turn. Conversational / read-only turns skip it entirely (no latency, no
   model call) → byte-identical.
2. **Deterministic layer (always)** — aggregate the per-tool `verified` ledger;
   account for delivered artifacts. Cheap, no model.
3. **Result gate + LLM-derived expected-outcome (only when promised)** — if the
   draft *claims* an outcome and the deterministic layer can't confirm it, a
   fast-tier model derives the expected outcome from the user request (general,
   no per-site logic — e.g. "a non-empty media file should exist under the
   workspace/downloads dir"), and the checker **observes the filesystem** to
   confirm. The derivation is a guess; the **check is real**. Result =
   verification, not representation.

The checker returns an `Acceptance{accepted: bool, reason}` verdict that:
- `objectives/driver.py` reads instead of "no error thrown" to mark a sub-goal
  `done` vs `failed`.
- the turn pipeline reads to set turn-level trustworthy success and to feed the
  honest floor (a not-accepted turn cannot ship a confident "done" draft).

### 5.3 Why this catches the `yt-dlp --simulate` class

Shell exited 0, no tool named an artifact, so the per-tool ledger looks clean. But
the behavior gate sees an effectful shell ran, the result gate sees the draft
claims "downloaded your video," the LLM-derived expected-outcome says "a non-empty
media file should exist," and the checker observes that **no such file exists** →
`accepted=False` → honest floor, no false win mined, sub-goal marked failed.

## 6. Positive-only learning

Positive-only learning (remember wins, never failures) **stays** — but the miner
and reflection trigger now key on `is_trustworthy_success` / the `verified`-aware
`task_outcomes` row, so a `verified=False` false win can never be mined. This is
the point at which positive-only becomes *safe* (it was a coping policy on an
untrustworthy signal). **Per owner directive, we do NOT change positive-only in
this branch; revisit only after explicit owner approval.**

## 7. Default state & config

- Tool-boundary verification: **ON** (deterministic fs checks; fail-safe to
  `verified=None`).
- Goal-level acceptance: **ON**, behavior+result gated. The LLM-derived layer uses
  the configured fast tier (no vendor-specific logic; `settings`-driven, mirrors
  `judge_tier`). A `settings.verification.*` block exposes enable flags and the
  acceptance tier, all defaulting to the ON posture.

## 8. Blast radius & byte-identical guarantees

- `ToolResult.verified` is additive with default `None`; `extra="forbid"` is
  preserved. All existing `ToolResult(...)` constructions remain valid.
- `Tool.__call__` only verifies when `verify()` is overridden → 56 non-tool +
  ~36 non-artifact tool files unchanged in behavior.
- `is_effectful_failure` / `consequential_tally` gain an optional `verified`
  param defaulting to `None` → every existing caller is byte-identical until it
  opts in.
- `PipelineState.expected_outcome`, `Subgoal.acceptance_criteria`,
  `ToolOutcome.verified` all default None/absent → unconfigured/legacy paths
  unchanged.
- `AcceptanceChecker` no-ops on conversational/read-only turns.

## 9. Testing strategy (TDD — failing tests first)

1. **Primitive:** a fake tool that returns `success=True` but produces nothing →
   after `__call__`, `result.verified is False`; `is_trustworthy_success` False.
2. **Artifact tools:** `write_file`/`image_generate`/`tts` that claim a path which
   is absent or zero-byte → `verified=False`; a real write → `verified=True`.
3. **Byte-identical:** a tool with no `verify()` override → `verified is None`,
   result object unchanged.
4. **Ledger/floor:** a `verified=False` write outcome trips
   `is_effectful_failure` and the consequential give-up floor; a `verified=True`
   does not.
5. **Learning:** a `verified=False` turn is NOT mined as a win; positive-only
   still mines `verified=True`.
6. **Acceptance authority:** the `yt-dlp --simulate` shape (effectful shell, draft
   claims a download, no file on disk) → `accepted=False`; a real download →
   `accepted=True`; a conversational turn → checker not engaged.
7. **Objectives:** a sub-goal whose acceptance fails → `failed`, not `done`.

Gates before "done": `tools`, `pipeline`, `runtime`, `journeys` suites green,
plus `ruff check src/` and `mypy src/` clean on changed files.

## 10. Non-goals (this branch)

- No new download/media tool (the capability exists; we fix the verification
  CLASS, not the symptom).
- No change to positive-only learning behavior (revisit later, owner-gated).
- No vendor-specific logic anywhere; the acceptance tier is config-driven.
- Windows catastrophic-path detection and other pre-existing cuts remain as-is.
