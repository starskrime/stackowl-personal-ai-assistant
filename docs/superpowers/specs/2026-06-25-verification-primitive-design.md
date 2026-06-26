# Verification Primitive — Design Spec (v2)

**Date:** 2026-06-25
**Branch base:** `feat/verification-primitive`
**Status:** approved-design, revised after adversarial party-mode review (Winston / Murat / Mary / Amelia)

> **This is a CORE architectural change, not a download fix.** The contract change
> is on `ToolResult` (the base type every ~100 tools return) and the seam is
> `Tool.__call__` (every dispatch). The `yt-dlp --simulate` incident is merely the
> cheapest reproducible *witness* of the class "claimed an effect, produced
> nothing." The word "download" appears nowhere in the code; we fix the class.

## 1. The root cause

StackOwl has no objective **verification** primitive. Every "success" is either
*asserted* (a self-reported boolean / process exit code) or *guessed* (an LLM
judge reading draft text) — never *measured against reality*. Because
`ToolResult.success` is unverified at its source, the delivery judge, the honesty
floors, the overclaim gate, the learning loop, and the objective planner all
reason on a signal that can be a lie.

Anchors: `shell.py:486` `success=(returncode==0)`; `write_file.py:86` `success=True`
never reads back; `image_generate.py:196` / `tts.py:193` on the backend's word;
`asyncio_backend.py:256` turn `success=(len(errors)==0)`; `objectives/driver.py:163`
sub-goal `done`=no error thrown (no acceptance criterion on `Subgoal`);
`persistence.py:366` judge reads TEXT, fails OPEN; `state.py` has no acceptance
field.

**Principle: verification > representation.** Lift `ToolResult.success` from
asserted to *verified-against-a-post-condition* at the universal tool seam, then
to a per-turn / per-goal acceptance check owned by ONE authority — collapsing the
disjoint honesty-proxy pile onto a single measured signal.

## 2. Sensor vs. actuator (the two roots)

The party review surfaced that verification alone makes the platform **honest, not
agentic** — it changes the agent's *belief*, not its *behavior*. An honest quitter
is still a quitter. So the design has two halves:

- **Phase A — the SENSOR (this spec's near-term deliverable):** measure whether an
  action achieved its effect. Resolves the false-success class.
- **Phase B — the ACTUATOR (designed here, built last):** a recovery policy that
  *consumes* `verified=False` and does something agentic — retry once, escalate to
  a stronger model, prefer a known-good learned skill, or stop-and-ask. This is
  what kills the flailing and earns "Jarvis." Reuses the escalation gateway and
  same-tool circuit breaker already half-built in the platform.

Without Phase B, flailing may temporarily *worsen* (the loop now knows it hasn't
succeeded and keeps trying). Phase B is non-optional for the agentic goal; it is
merely sequenced last because it depends on the sensor existing first.

## 3. Decisions (owner-settled)

| Fork | Decision |
|------|----------|
| Mechanism | `verify()` hook on `Tool` ABC + `verify_artifact()` helper as the common-case shorthand |
| Failure semantics | Separate `verified` field; do NOT mutate `success` (preserve claimed-vs-confirmed) |
| Scope | Full agentic solution: sensor (Phase A) AND goal-level acceptance, with the actuator (Phase B) designed and sequenced last |
| Acceptance source | Both: deterministic per-tool/per-goal observation always on; LLM-derived expected-outcome behavior+result gated, **fail-closed, flag-OFF until measured** |
| Default state | Phase-A deterministic verification ON; Phase-2 LLM-derived acceptance behind a flag (default OFF) |
| Oracle strength | `verified=True` requires existence **+ freshness + magic-byte/MIME** for typed artifacts — never bare "non-empty" |

## 4. The contract

### 4.1 `ToolResult` additive fields (frozen model, `extra="forbid"`)

```python
class ToolResult(BaseModel):
    success: bool                         # unchanged: the tool's self-report
    verified: bool | None = None          # NEW reality check (None=not-checked)
    artifact_path: str | None = None      # NEW structured locator (Amelia's fix)
    ...
```

- `verified`: `None` → not checked (byte-identical); `True` → effect observed;
  `False` → claimed but reality disagreed.
- `artifact_path`: the tool's OWN trusted path to what it produced, so `verify()`
  reads a structured value instead of re-parsing `output` text (which would be the
  very "GUESSED" failure mode this spec kills). Tools that produce a file set it;
  others leave it `None`.

### 4.2 One derived predicate, read everywhere

```python
def is_trustworthy_success(success: bool, verified: bool | None) -> bool:
    return success and verified is not False
```

`verified is False` → not trustworthy → floor fires, learning skips, judge sees
fail. `verified is None` → falls back to `success` → today's behavior exactly.

### 4.3 `verify()` hook + hardened `verify_artifact()`

`Tool.verify(self, args, result) -> bool | None` on the ABC (default `None`).
Runs ONLY after `success=True`; never raises; result falls back to `None` on any
error (fail-safe — verification never blocks a real success it merely failed to
confirm).

`tools/verification.py` (new):

```python
def verify_artifact(
    path: str | Path | None,
    *,
    not_before: float | None = None,         # tool-call start (monotonic/epoch)
    expect_kind: str | None = None,          # "image"|"audio"|"pdf"|... or None
) -> bool | None:
    """True iff path is a non-empty regular file that is THIS run's artifact and
    (when expect_kind given) passes a magic-byte/MIME sanity check.
    None when path is None/empty. Never raises."""
```

- **Existence + non-empty** — `getsize() > 0`.
- **Freshness** — when `not_before` is given, the file's mtime must be ≥ it, so a
  stale last-run artifact on a predictable path cannot pass (Murat's #1 risk).
- **Magic-byte/MIME** — when `expect_kind` is given, a cheap header check rejects a
  9-byte error page saved as `.mp4` (Murat's #2). Content *correctness* (right
  subject) remains out of scope and is documented as a known ceiling.

`Tool.__call__` stamps the bit:

```python
result = await self.execute(**kwargs)
if result.success and result.verified is None:
    try:
        verdict = await self.verify(kwargs, result)
    except Exception:
        verdict = None
    if verdict is not None:
        result = result.model_copy(update={"verified": verdict})
return result
```

A tool that does not override `verify()` → `None` → unchanged. **Byte-identical**
for the ~92 un-migrated tool/non-tool files.

## 5. Branches (staged by blast radius — Amelia)

### Branch 1 — primitive + existence-class (ON by default, low risk)
- `ToolResult.verified` + `artifact_path`; `is_trustworthy_success`;
  `tools/verification.py` with freshness + magic-byte; `Tool.verify()` hook +
  `__call__` stamping.
- Migrate the **existence-class** tools that name their own artifact:
  `write_file`, `image_generate`, `tts`, `get_images`, `send_file` (send_file
  verifies the LOCAL file it claims to send; delivery is `unconfirmed` coupling).
- `verified` is **read-but-not-floor-wired** in B1 — stamped and surfaced, but the
  giveup floor still reads the old predicate. This isolates the headline
  empty/stale-artifact fix with zero journey-fixture risk.
- **RED tests first:** stale-file → `verified=False`; empty-file → `False`; missing
  → `False`; real fresh artifact → `True`; magic-byte mismatch → `False`;
  no-override tool → `verified is None` (byte-identical).

### Branch 2 — floor wiring + content-class (journey-suite gate)
- Thread `verified` through `infra/tool_outcome_ledger.py`: `ToolOutcome.verified`;
  `is_effectful_failure(..., verified=None)` treats `verified is False` as an
  effectful failure; `consequential_tally` counts it as a failure;
  `execute.py:1053` passes `verified=tr.verified`.
- **Deletion list** (Winston): remove/redirect the redundant proxy re-derivations
  that now read the one ledger signal, so the pile collapses rather than grows.
  (Enumerated during B2 against the live code; each deletion gated by a test.)
- Migrate **content-class** tools with real post-conditions (not `verify_artifact`):
  `edit` (post-condition string present, pre-condition consumed), `apply_patch`
  (all hunks applied, file parses), `pdf` (page-count / extractable). Each ~20–60
  lines + its own RED test.
- **Gate:** full `journeys` suite green (this is where the 19-fixture class of
  break lives — isolate it here).

### Branch 3 — goal-level acceptance authority (Phase-2 LLM flag-OFF)
- `PipelineState.expected_outcome` + `Subgoal.acceptance_criteria` (additive,
  default None = byte-identical).
- `pipeline/acceptance.py` — the ONE `AcceptanceChecker`. Behavior-gated (engages
  only if an effectful tool ran). **Deterministic layer always on**: observe the
  declared `expected_outcome` artifact on the filesystem. This catches the
  shell/effectful class (incl. the yt-dlp witness) **deterministically**, because
  the turn declared the expected artifact UP FRONT — no LLM derivation needed.
- **LLM-derived expected-outcome: result-gated, fail-CLOSED, flag-OFF by default.**
  When enabled it derives a criterion only when the draft claims an outcome the
  deterministic layer couldn't confirm, then OBSERVES reality. If the model is
  unavailable/times out → **no positive acceptance asserted** (honest-limit), never
  a silent pass. Runs **post-hoc** (feeds learning + the next-turn floor), NOT as a
  pre-delivery latency gate.
- Wire `objectives/driver.py:163` to read the acceptance verdict (sub-goal `done`
  vs `failed`) instead of "no error thrown"; feed turn-success.

### Branch 4 — recovery actuator (Phase B, earns "Jarvis")
- A `RecoveryPolicy` that consumes `verified=False` / `accepted=False`:
  retry-once → escalate model → prefer a known-good learned skill → stop-and-ask.
- Reuse the existing escalation gateway and same-tool circuit breaker.
- Plus a **one-time back-catalog re-validation pass**: re-check already-learned
  tools against `is_trustworthy_success`; evict/flag the ones that only ever
  produced false wins (the `instagram_media_extractor` class). Confirm the learner
  reads `is_trustworthy_success`, not raw `success` (the `registered≠reachable`
  trap).

## 6. Positive-only learning

Unchanged in behavior (owner directive: never learn failures). But the miner /
reflection trigger key on `is_trustworthy_success` (B2/B4), so a `verified=False`
false win can never be mined. This is the point at which positive-only becomes
*safe*. **Do not alter positive-only without explicit owner approval.**

## 7. Byte-identical guarantees

- `ToolResult.verified` / `artifact_path` additive, default `None`; `extra="forbid"`
  preserved; all existing constructions valid.
- `Tool.__call__` verifies only when `verify()` is overridden.
- `is_effectful_failure` gains optional `verified=None` → existing callers
  unchanged until they opt in.
- `PipelineState.expected_outcome`, `Subgoal.acceptance_criteria`,
  `ToolOutcome.verified` default None/absent.
- `AcceptanceChecker` no-ops on conversational/read-only turns; LLM layer flag-OFF.

## 8. Testing strategy (TDD — RED first)

Per branch, mirror the FAILURE not the happy path:
1. **Primitive:** `is_trustworthy_success(True, None) is True`; `(True, False) is
   False`. Fake tool claims success, produces nothing → `verified is False`.
2. **Stale-file (the test that fails a naive impl):** pre-seed a file, tool no-ops,
   assert `verified is False` via freshness.
3. **Magic-byte:** 9-byte text saved with an image extension → `verified is False`.
4. **Byte-identical:** no-override tool → `verified is None`, result unchanged;
   full journey suite green as the B2 gate.
5. **Ledger/floor (B2):** `verified=False` write trips `is_effectful_failure` and
   the consequential floor; `verified=True` does not.
6. **Acceptance (B3):** declared-artifact turn with no file on disk →
   `accepted=False`; with file → `True`; conversational → checker not engaged;
   LLM-layer unavailable → honest-limit, never silent pass.
7. **Objectives (B3):** sub-goal whose acceptance fails → `failed`, not `done`.

Gates before "done" per branch: `tools`, `pipeline`, `runtime`, `journeys` green +
`ruff check src/` + `mypy src/` clean on changed files.

## 9. Non-goals

- No new download/media tool (fix the class, not the symptom).
- No change to positive-only learning behavior (owner-gated).
- No vendor-specific logic; the acceptance tier is config-driven (mirrors
  `judge_tier`).
- Semantic correctness of artifacts (right subject / right answer) is a known
  ceiling of filesystem verification, deferred to the LLM acceptance layer and
  human-eval; documented, not silently claimed.
