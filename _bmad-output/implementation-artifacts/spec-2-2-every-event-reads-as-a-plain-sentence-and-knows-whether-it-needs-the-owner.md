---
title: 'Every event reads as a plain sentence and knows whether it needs the owner'
type: 'feature'
created: '2026-09-17'
status: 'done'
baseline_revision: '0ec7371e42a1fe02b2e7c5655d53973d8cb1ebee'
review_loop_iteration: 0
followup_review_recommended: true
context: []
warnings: ['oversized']
deferred:
  - summary: >-
      task_events.py's comment on task.dead_lettered says it is "one of the
      two NAMED needs_you/high examples", but AD-5 now names three
      (heal.exhausted, task.dead_lettered, job.parked).
    evidence: |-
      Pre-existing comment from Story 2.1 (unchanged context in Story 2.2's
      diff, not modified by it). ARCHITECTURE-SPINE.md's current AD-5 text:
      "Only explicit give-up or unhealed event types (such as heal.exhausted,
      task.dead_lettered, job.parked)... are needs_you at high" — three named
      examples via "such as", not two. Cosmetic-only: the comment does not
      affect behavior, only its own accuracy.
    location: >-
      src/stackowl/journal/task_events.py (comment above the task.dead_lettered
      registration)
    severity: low
---

<intent-contract>

## Intent

**Problem:** Story 2.1's four task-lifecycle event types record with `attention`/`intensity` always `NULL` (no policy computes them) and no plain-English rendering exists anywhere — every future surface (Bridge, briefing, Telegram, Web Push) would otherwise invent its own wording and its own ambient/needs-you judgment, disagreeing with each other.

**Approach:** Add one pure attention policy (`journal/attention.py`) that classifies a registered type as `ambient` or `needs_you`+`intensity` purely from registry metadata, wired into `journal.record()` so it — never the emitter — sets the envelope's `attention`/`intensity`. Add one narrator (`journal/narrator.py`) that renders a registered type's `full` sentence (via a per-type `narrate` callable declared at registration, same site as `attention_class`) and a generic `public` rendering (kind + count only), resolving current names through `NameResolver` ports subsystems register into `journal/` (AD-30), falling back to a tombstone name when a target is gone or a resolver is unregistered/raises. Wire `pipeline.durable` as the first (and today only) `NameResolver` registrant, keyed by `RecordKind`.

## Boundaries & Constraints

**Always:** Attention classification is a pure, synchronous, in-memory registry lookup — no DB/network I/O on the write path (AD-5). `journal.record()` computes `attention`/`intensity` itself from the registry and raises loudly if an emitter pre-set either field on the `JournalEvent` it hands in. Every `EventTypeSpec` registration declares both `attention_class` and a `narrate` callable — both required, both validated at registration time so a missing one fails loudly, not silently. `intensity` is required (`NORMAL`/`HIGH`) when `attention_class` is `NEEDS_YOU` and forbidden (`None`) when `AMBIENT`. The narrator's name resolution (I/O, at delivery time — not the write path) never raises: a missing resolver, a resolver returning `None`, or a resolver that raises is caught and rendered as a tombstone (`"a retired {kind}"`). `journal/` still imports nothing from any subsystem; `NameResolver` ports are registered INTO `journal/` by the owning subsystem (`pipeline.durable`), inverting the dependency (AD-7, AD-30).

**Never:** No Needs-you item is opened here — that is Epic 3's consumer of `attention == needs_you` (epic-2-context.md's own Cross-Story Dependencies). No gateway push, record readers, retention, or a live delivery surface (Bridge/Telegram/Web Push) — none exist yet; the narrator is proven by direct unit calls, not end-to-end delivery. No new record kinds or event types beyond the four Story 2.1 already registered. No free-text/message content in any resolved name beyond the existing 64-char bounded-label convention (`_MAX_LABEL_LEN`).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Ambient type recorded | `task.enqueued` recorded via `record()` | Stored row has `attention='ambient'`, `intensity=NULL` | n/a |
| Give-up type recorded | `task.dead_lettered` recorded via `record()` | Stored row has `attention='needs_you'`, `intensity='high'` | n/a |
| Emitter pre-sets attention | `JournalEvent(..., attention="ambient")` passed to `record()` | No row inserted | Raises `JournalAttentionSetByEmitterError` before any SQL runs |
| Registering a type with no narration | `EventTypeSpec(..., narrate=None)` | Registration refused | Raises at construction (`__post_init__`), before `registry.register()` |
| Registering `NEEDS_YOU` with no intensity | `EventTypeSpec(attention_class=NEEDS_YOU, intensity=None, ...)` | Registration refused | Raises at construction |
| Narrate a live task event | `narrate(task.claimed event)` with a registered `NameResolver` and an existing task | `full` names the task; `public` says kind only | n/a |
| Narrate after the target is gone | Same, but `store.get(task_id)` raises `DurableTaskNotFoundError` | `full` reads `"a retired task"` (or similar) | No exception — tombstone substituted |
| Narrate with no resolver registered | `record_kind` has no `NameResolver` registered | Same tombstone fallback | No exception |
| Narrate with a resolver that raises | Resolver itself throws (unexpected error) | Same tombstone fallback, logged with 4-point logging | Logged, never propagated |

</intent-contract>

## Code Map

- `src/stackowl/journal/registry.py:22-38` (`EventTypeSpec`) -- add two fields in this order (dataclass field-ordering: non-default before default): `narrate: Callable[[JournalAttrsBase, str], str]` (required, no default) after `attention_class`, then `intensity: Intensity | None = None` last. Add `__post_init__` validating: `narrate` is callable; `attention_class == NEEDS_YOU` requires `intensity is not None`; `attention_class == AMBIENT` requires `intensity is None`. Frozen dataclass — `__post_init__` may validate but not assign new fields.
- `src/stackowl/journal/enums.py:63-73` (`AttentionClass`) -- add `class Intensity(StrEnum): NORMAL = "normal"; HIGH = "high"` immediately after it, same closed-vocabulary style.
- `src/stackowl/journal/models.py:50-77` (`JournalEvent`) -- `attention`/`intensity` fields already exist (optional, caller-settable today); no model change needed, only the recorder's trust in them changes.
- `src/stackowl/journal/recorder.py:60-137` (`record()`) -- after `spec = get_registry().get(event.type)` (line 80) and the `attrs` isinstance check: if `event.attention is not None or event.intensity is not None`, log + raise `JournalAttentionSetByEmitterError(event.type)` before any further work. Otherwise call the new `attention.classify(event.type)` and use its returned `(AttentionClass, Intensity | None)` -- not `event.attention`/`event.intensity` -- in the `_INSERT_SQL` params (lines 111-117).
- `src/stackowl/journal/attention.py` (NEW) -- `def classify(type_name: str) -> tuple[AttentionClass, Intensity | None]`: `spec = get_registry().get(type_name); return spec.attention_class, spec.intensity`. Pure, synchronous, in-memory only (AD-5's "no I/O on the write path").
- `src/stackowl/journal/narrator.py` (NEW) -- `NameResolver = Callable[[str], Awaitable[str | None]]`; a dict-keyed-by-`RecordKind` registry (`register_name_resolver`, raises on duplicate per kind -- mirrors `registry.py`'s duplicate-`type` refusal; `reset_name_resolvers_for_tests()` mirrors `journal/health.py:68`'s `reset_for_tests()`); `async def _resolve_name(record_kind, target_id) -> str` -- returns the resolver's name, or `f"a retired {record_kind.value}"` when no resolver is registered, the resolver returns `None`, or the resolver raises (caught, 4-point logged, never propagated); `NarrationResult` (small frozen model: `full: str`, `public: str`); `async def narrate(event: JournalEvent, locale: str = "en") -> NarrationResult` (only `"en"` supported today, per `reviews/review-adversary.md:360`'s exact `narrate(event, locale)` signature) -- looks up the spec, resolves the name, calls `spec.narrate(event.attrs, name)` for `full`, and a generic `f"1 {spec.record_kind.value} update"`-style sentence for `public` (kind + count only, no name). Also export `narrate_full`/`narrate_public` as the two underlying pieces for future batched/count use.
- `src/stackowl/journal/task_events.py:78-105` (`_register()`) -- add `intensity=None` to the three `AMBIENT` registrations (`task.enqueued`, `task.claimed`, `task.finished`) and `intensity=Intensity.HIGH` to `task.dead_lettered`. Add four `narrate=` callables above `_register()`, one per type, signature `(attrs: JournalAttrsBase, name: str) -> str` (accept the base type, `isinstance`-narrow or `cast()` inside -- `Callable[[JournalAttrsBase, str], str]` is what `EventTypeSpec.narrate` declares, and a narrower-accepting function is not assignable to it under mypy's contravariance rule). Plain-English sentences, e.g. `f"Task {name} was queued."`, `f"Task {name} was claimed."`, `f"Task {name} finished."`, `f"Task {name} gave up after {attrs.attempt_count} attempts."`.
- `src/stackowl/journal/__init__.py:27-47` -- export `Intensity`, `classify` (from `attention.py`), `narrate`, `narrate_full`, `narrate_public`, `NarrationResult`, `register_name_resolver`, `reset_name_resolvers_for_tests`.
- `src/stackowl/exceptions.py:629-648` (near `JournalEventTypeUnregisteredError`) -- new `JournalAttentionSetByEmitterError(DomainError)`, `.remedy` set in `__init__` (matches the exact fix Story 2.1's review applied to its sibling exceptions): `"stop passing attention/intensity when constructing this event -- journal.record() computes them from the registry (AD-5)"`.
- `src/stackowl/pipeline/durable/store.py:410-436` (`DurableTaskStore.get()`) -- raises `DurableTaskNotFoundError` on a miss; the task name resolver below catches exactly this.
- `src/stackowl/pipeline/durable/journal_names.py` (NEW) -- `async def resolve_task_name(store: DurableTaskStore, task_id: str) -> str | None` (catches `DurableTaskNotFoundError` -> `None`; otherwise `task.goal[:64]`, matching the existing `_MAX_LABEL_LEN=64` bound); `def register_task_name_resolver(db_pool: DbPool, owner_id: str = DEFAULT_PRINCIPAL_ID) -> None` builds one `DurableTaskStore` and calls `journal.register_name_resolver(RecordKind.TASK, ...)` with a closure over it.
- `src/stackowl/startup/orchestrator.py:859` (`_phase_gateway`), `:1829` (`durable_task_store=DurableTaskStore(db_pool)`) -- call `register_task_name_resolver(db_pool)` once in this method, alongside the existing `DurableTaskStore(db_pool)` construction, so the live process has a working task-name resolver at boot.
- `tests/journal/conftest.py` -- existing autouse `_reset_journal_health` fixture; extend it (or add a sibling autouse fixture) to also call `reset_name_resolvers_for_tests()` per test, matching the existing per-test process-global reset convention.

## Tasks & Acceptance

**Execution:**
- `src/stackowl/journal/enums.py` -- add `Intensity` enum -- AD-5's closed intensity vocabulary.
- `src/stackowl/journal/registry.py` -- `EventTypeSpec` gains `narrate`/`intensity`, `__post_init__` validation -- makes "no attention class" / "no narration" / "needs_you with no intensity" refuse at registration, not silently pass.
- `src/stackowl/journal/attention.py` -- `classify()` -- the one pure attention policy (AD-5).
- `src/stackowl/journal/recorder.py` -- guard against emitter-set `attention`/`intensity`; compute both from `classify()` -- AC's "computed purely from its registered class... emitters never classify."
- `src/stackowl/journal/narrator.py` -- `NameResolver` registry, tombstone fallback, `narrate()`/`narrate_full()`/`narrate_public()` -- AD-30's one narrator.
- `src/stackowl/journal/task_events.py` -- add `narrate=`/`intensity=` to all four registrations -- gives every existing type a narration and a complete classification.
- `src/stackowl/journal/__init__.py` -- export the new public surface.
- `src/stackowl/exceptions.py` -- `JournalAttentionSetByEmitterError` with `.remedy` set.
- `src/stackowl/pipeline/durable/journal_names.py` -- the task `NameResolver` implementation and its registration helper.
- `src/stackowl/startup/orchestrator.py` -- wire `register_task_name_resolver(db_pool)` into `_phase_gateway`.
- `tests/journal/conftest.py` -- extend the autouse reset fixture to cover the name-resolver registry too.
- `tests/journal/test_recorder.py` -- extend: recording `task.dead_lettered` yields `attention='needs_you', intensity='high'`; recording `task.enqueued` yields `attention='ambient', intensity=NULL`; a `JournalEvent` with `attention`/`intensity` pre-set raises `JournalAttentionSetByEmitterError` before any SQL runs (mark `@pytest.mark.tripwire` -- this is a cross-cutting invariant, not a path-selected test, per `scripts/tripwires.sh`'s own stated rationale).
- `tests/journal/test_registry_and_leak_guard.py` -- extend: `EventTypeSpec(..., narrate=None)` raises; `EventTypeSpec(attention_class=NEEDS_YOU, intensity=None, ...)` raises; `EventTypeSpec(attention_class=AMBIENT, intensity=Intensity.HIGH, ...)` raises. Mark `@pytest.mark.tripwire`.
- `tests/journal/test_attention.py` (NEW) -- `classify()` returns `(AMBIENT, None)` for the three ambient task types and `(NEEDS_YOU, HIGH)` for `task.dead_lettered`; `classify` is synchronous (proxy for "no I/O on the write path").
- `tests/journal/test_narrator.py` (NEW) -- `narrate()` produces a plain sentence per task event type with a real registered `NameResolver` returning a known name; `public` mentions kind only, never the name; no resolver registered / resolver returns `None` / resolver raises -> tombstone text, never an exception; every declared type in `get_registry().all_types()` has a non-`None` `narrate` (coverage tripwire, mark `@pytest.mark.tripwire`); grep-based tripwire asserting `EventTypeSpec(` is constructed only inside `src/stackowl/journal/` (mark `@pytest.mark.tripwire` -- "any surface that stores its own sentence for an event").
- `tests/pipeline/durable/test_journal_names.py` (NEW) -- `resolve_task_name()` returns a name derived from `goal` for an existing task and `None` for a missing one; `register_task_name_resolver()` wired end-to-end with a real `tmp_db`/`DurableTaskStore`, driven through `journal.narrate()`.

**Acceptance Criteria:**
- Given a registered event type, when the attention policy classifies it, then the result is `ambient` or `needs_you`+intensity, computed purely from its registered class with no I/O on the write path; failure/heal-in-progress types are `ambient`; only give-up types (`task.dead_lettered`) are `needs_you` at `high`.
- Given the codebase, when the tripwires run, then they fail any emitter that sets `attention`/`intensity` itself, and any registered type without an attention class.
- Given a recorded event, when the narrator renders it at delivery time, then it produces a `full` sentence and a `public` rendering (kind+count only) using current names from registered `NameResolver` ports, and `journal/` imports no subsystem; a target that no longer exists renders with its tombstone name, never an error.
- Given the registry, when the tripwires run, then they fail any registered event type without a narration, and any surface that stores its own sentence for an event.

## Spec Change Log

## Review Triage Log

### 2026-09-17 — Review pass
- verdicts: 18 findings — high 0, medium 3, low 7, false 8, maybe-false 0
- findings:
  - `[low]` `[defer]` (blind-hunter) `task_events.py`'s pre-existing comment on `task.dead_lettered` claims it is "one of the two NAMED needs_you/high examples," but AD-5 now names three (`heal.exhausted`, `task.dead_lettered`, `job.parked`) — evidence: verified the comment lines are unchanged context in this diff (no `+`/`-` prefix), written by Story 2.1, not this story; the architecture spine's current AD-5 text does name three via "such as". Not caused by this story — routed to defer, logged to `deferred-work.md`.
  - `[false]` `[reject]` (blind-hunter) `narrate()`/`narrate_full()`/`narrate_public()`/`register_task_name_resolver` have no production caller, so nothing is observable yet — evidence: refuted — `epic-2-context.md`'s own Cross-Story Dependencies state Story 2.2's narrator is a prerequisite for later stories (2.5 TUI progress, 2.9 deliveries, Epic 3 Needs-you items) that wire it to a live delivery surface; the intent itself (the epic's own story sequencing), not just this spec's scope note, draws this boundary.
  - `[low]` `[patch]` (blind-hunter) `recorder.py`'s `record()` looks up the registry twice for the same `event.type` (`spec = get_registry().get(...)` then `classify(event.type)` re-derives the same spec) — evidence: verified at `recorder.py`'s new lines; `spec` is already in scope. Fix applied: reuse `spec.attention_class`/`spec.intensity` directly instead of calling `classify()` a second time.
  - `[low]` `[patch]` (blind-hunter) `journal_names.py` redefines `_MAX_LABEL_LEN = 64` as its own literal instead of importing the existing constant from `task_events.py`, while its own comment says "same value as... `_MAX_LABEL_LEN`" — evidence: verified duplication; a future AD-4 bound change would silently drift between the two. Fix applied: import/reuse the shared constant.
  - `[low]` `[patch]` (blind-hunter) `resolve_task_name()`'s 4-point logging comments skip `# 3. STEP` (goes `# 1. ENTRY` → `# 2. DECISION` → `# 4. EXIT`) — evidence: verified against the numbering convention correctly followed elsewhere in this same diff (`recorder.py`, `narrator.py`). Fix applied: added the missing `# 3. STEP` comment.
  - `[false]` `[reject]` (blind-hunter) `narrate(event, locale="en")`'s `locale` parameter does nothing but get logged, giving the appearance of i18n support that isn't implemented — evidence: refuted — this is the exact architecture-mandated signature (`reviews/review-adversary.md:360`'s M2 fix: "The signature is `narrate(event, locale)`, with `en` as the only locale for now"), and silently rendering rather than raising on an unsupported value matches the same "narration never fails" pattern this story establishes everywhere else (tombstone fallback, resolver-raise fallback).
  - `[false]` `[reject]` (blind-hunter) `Intensity.NORMAL` is added/exported but no registered type or test ever uses it — dead code as shipped — evidence: refuted by direct precedent — Spec 2.1's own Review Triage Log rejected the identical claim about `EventTypeSpec.record_kind` ("a declared-but-not-yet-consumed registry field with a stated reason is normal incremental-registry design"); `Intensity.NORMAL` is the same additive, forward-looking closed-vocabulary member AD-5 requires ("intensity is normal or high"), just with no `NEEDS_YOU`-at-`NORMAL` type registered yet.
  - `[low]` `[reject]` (blind-hunter) `test_EventTypeSpec_is_constructed_only_inside_journal` uses a regex over full file text rather than an AST check, so it could false-positive on a future docstring/comment merely mentioning `EventTypeSpec(...)` outside `journal/` — evidence: real gap in principle, but no file today contains that text outside `journal/` (verified — the test passes); a robust fix needs AST parsing, more than a direct correction, for a scenario nothing currently reaches. Rejected per the low-finding rule.
  - `[false]` `[reject]` (blind-hunter) `register_task_name_resolver` is documented as "called once at boot" but would crash startup if `_phase_gateway` were ever re-entered in-process (e.g. a future self-heal restart) — evidence: refuted — no call path today re-invokes `_phase_gateway` within one process; this is the same "runs once at import/boot" assumption `task_events.py`'s `_register()` and `scheduler/assembly.py`'s health-contributor registration already rely on without incident. Code that fails loudly on a situation nothing shows the program can reach is correct behavior, not a defect.
  - `[false]` `[reject]` (intent-alignment) nothing in this diff is committed yet (still `status: in-review`) — evidence: refuted — this is the correct, intended mid-workflow state; this review step runs deliberately before the Finalize/commit step, not after.
  - `[low]` `[reject]` (intent-alignment) the spec's own manual check (drive a real task through `scripts/dev_ingress.py`, call `journal.narrate()` against the live DB) was not performed — evidence: the automated end-to-end test (`test_journal_names.py`'s `test_narrate_names_a_real_task_by_its_goal`) already exercises the identical code path against a real `DurableTaskStore`/`tmp_db`. Checked whether this session could run it directly: no live gateway/core process is running in this environment (`ps aux` shows none; `~/.stackowl/runtime/` has no `core.sock`, only a stale `dev-ingress.sock` from two days before this story), and `dev_ingress.py` only injects into an already-running gateway — it cannot exercise this diff's code until the live process is restarted onto it, which the dispatching instructions explicitly reserve for the coordinator. Correctly left to the coordinator after deployment, not performable or closeable from this session.
  - `[medium]` `[patch]` (intent-alignment) several new/changed methods carry no 4-point logging at all (`attention.classify()`, `EventTypeSpec.__post_init__`, `narrator.register_name_resolver()`, `narrate_full()`, `narrate_public()`, `_tombstone()`) — evidence: verified directly against each function; the dispatching house rule is unqualified ("4-point structured logging on every new/changed method... no exceptions"). Real harm named: an incident during registration-time refusal or classification has no log trail to diagnose from, unlike every other journal/ write path. Fix applied: added entry/decision/step/exit logging (scaled to each function's actual branching) to all six.
  - `[false]` `[reject]` (intent-alignment) `EventTypeSpec.__post_init__` and `register_name_resolver`'s duplicate-registration check both raise bare `ValueError` rather than a `DomainError` subclass, unlike `JournalAttentionSetByEmitterError` — evidence: refuted — `registry.py`'s own `EventRegistry.register()` (Story 2.1) already raises bare `ValueError` for the same class of registration-time-misuse; runtime `record()`-path failures get `DomainError`/`.remedy` because they flow through `remedy_for()` and the health contributor, which registration-time (import-time, developer-visible) failures never do. Consistent with established precedent, not a new inconsistency.
  - `[false]` `[reject]` (intent-alignment) AD-30's "each command read-back" half is not covered by the narrator — evidence: refuted — `CommandContext`/`CommandSpec` (the read-back carrier) doesn't exist yet per Spec 2.1's own Design Notes ("Epic 4"); the intent's own epic sequencing, not this spec, places command read-back narration after infrastructure this story cannot build.
  - `[false]` `[reject]` (intent-alignment) `deferred-work.md` gets no new entry from this story — evidence: addressed directly — this review pass's own `defer`-routed finding (the AD-5 comment above) is logged to `deferred-work.md` as part of this same pass.
  - `[medium]` `[patch]` (edge-case-hunter) `narrate()` never validates `event.attrs` against `spec.attrs_model` before calling `spec.narrate(attrs, name)` — a `JournalEvent` built with mismatched `type`/`attrs` (not routed through `record()`'s own check) crashes with an uncaught `AttributeError`/`TypeError` inside the type-specific `narrate` callable instead of failing loudly and clearly — evidence: verified directly against `narrator.py`; `recorder.py` already guards exactly this case for the write path, `narrate()` does not for the read path. Fix applied: added the same `isinstance` check, raising the existing `JournalInvalidAttrsError`.
  - `[medium]` `[patch]` (verification-gap, pre-verified) no test proves `_phase_gateway` actually calls `register_task_name_resolver(db_pool)` at boot; a regression that silently removed that line would ship with a fully green suite, and every task event would then always tombstone in `narrate()` undetected — evidence: pre-verified by the reviewer's own read of `tests/startup/` (no reference to `journal_names`/`register_task_name_resolver`) and `tests/journeys/test_memory_fix_guards.py` (runs `_phase_gateway` for real but asserts nothing about this path); filed disposition `patch` accepted. Fix applied: added a boot-wiring test.
  - `[low]` `[patch]` (verification-gap, "Other findings") `tests/journeys/test_memory_fix_guards.py::test_guard_memory_command_registered_via_orchestrator` runs the real `_phase_gateway()`, registering a real task-name resolver into `journal/narrator.py`'s process-global `_resolvers` dict with no teardown reset, unlike `tests/journal/conftest.py`'s and `test_journal_names.py`'s own autouse resets — evidence: verified; no current test collides with this (no other unprotected test calls `register_name_resolver`), so no demonstrated failure today, but it is real, order-dependent global-state leak with a trivial fix. Fix applied: added a `reset_name_resolvers_for_tests()` teardown to that test.

## Design Notes

`EventTypeSpec.narrate` is declared per type at the SAME registration call site as `attention_class` (in `task_events.py`), not in a separate side-table -- keeps "one type, one declaration" (AD-3) intact and makes "no narration" and "no attention class" the same kind of refusal (a required constructor argument, validated in `__post_init__` since Python dataclasses don't enforce field types at runtime).

`NameResolver` ports are keyed by `RecordKind` (the domain-entity vocabulary), not `ActorKind`/`target_kind` (the principal-category vocabulary Story 2.1's review already settled is NOT a domain taxonomy -- see spec-2-1's Review Triage Log, `[false][reject]` "target_kind missing a task member"). `reviews/review-adversary.md:360`'s illustrative `NameResolver(target_kind)` predates that settled distinction; this spec follows the settled registry vocabulary instead.

Tombstone text is generic and owned entirely by `journal/narrator.py` (`f"a retired {record_kind.value}"`) -- never per-subsystem -- so it degrades gracefully even for a `RecordKind` with no registered resolver at all.

## Verification

**Commands:**
- `uv run pytest tests/journal/ tests/pipeline/durable/test_journal_names.py tests/pipeline/durable/test_journal_wiring.py -q` -- expected: all new and existing tests pass.
- `uv run pytest -m tripwire -q` -- expected: passes, including the new tripwire-marked tests for this story.
- `./scripts/tripwires.sh` -- expected: exits 0 (ruff/mypy baselines unchanged or improved, boundary checks pass, all `-m tripwire` tests pass).

**Manual checks (if no CLI):**
- Drive a real task through `scripts/dev_ingress.py` (enqueue -> claim -> finish, or force a dead-letter) and call `journal.narrate()` on the resulting rows to confirm real plain-English sentences and correct `attention`/`intensity` values in the live SQLite DB.

## Auto Run Result

**Summary of implemented change:** Added one pure attention policy (`journal/attention.py::classify()`) computing `attention`/`intensity` purely from the registry, wired into `journal.record()` so emitters can never set either field themselves (raises `JournalAttentionSetByEmitterError` if they try). Added one narrator (`journal/narrator.py`) rendering a `full` plain-English sentence and a generic `public` (kind+count) rendering per event, resolving current names through a `NameResolver` port registry keyed by `RecordKind`, with a `"a retired {kind}"` tombstone fallback that never raises (missing resolver, `None`, or a raising resolver all degrade to the same text). `EventTypeSpec` now requires a `narrate` callable and a validated `intensity` at registration, refusing at construction if either is missing/inconsistent. Wired `pipeline.durable` as the first `NameResolver` registrant (task names from `goal`, 64-char bounded, `DurableTaskNotFoundError` -> tombstone), registered once at gateway boot.

**Files changed:**
- `src/stackowl/journal/attention.py` (new) -- `classify()`, the one pure attention policy.
- `src/stackowl/journal/narrator.py` (new) -- `NameResolver` registry, tombstone fallback, `narrate()`/`narrate_full()`/`narrate_public()`, `NarrationResult`, attrs-model validation before rendering.
- `src/stackowl/pipeline/durable/journal_names.py` (new) -- the task `NameResolver` implementation and its boot-time registration helper.
- `src/stackowl/journal/enums.py` -- new `Intensity` (`NORMAL`/`HIGH`).
- `src/stackowl/journal/registry.py` -- `EventTypeSpec` gains required `narrate` and validated `intensity`, with `__post_init__` refusing an incomplete/inconsistent registration.
- `src/stackowl/journal/recorder.py` -- `record()` computes `attention`/`intensity` from the registry itself and refuses an emitter-set value before any SQL runs.
- `src/stackowl/journal/task_events.py` -- all four task event types get `narrate=`/`intensity=`.
- `src/stackowl/journal/__init__.py` -- exports the new public surface.
- `src/stackowl/exceptions.py` -- `JournalAttentionSetByEmitterError` with `.remedy` set.
- `src/stackowl/startup/orchestrator.py` -- `_phase_gateway` registers the task `NameResolver` once, right after `db_pool.open()`.
- `tests/journal/conftest.py` -- autouse reset for the name-resolver registry, mirroring the existing journal-health reset.
- `tests/journal/test_recorder.py`, `tests/journal/test_registry_and_leak_guard.py` -- extended with attention/intensity computation and `EventTypeSpec` validation tests.
- `tests/journal/test_attention.py`, `tests/journal/test_narrator.py` (new) -- policy purity, narration correctness, tombstone-on-raise/`None`/missing-resolver, attrs-mismatch validation, and two coverage tripwires.
- `tests/pipeline/durable/test_journal_names.py` (new) -- task-name resolution and end-to-end wiring through `journal.narrate()`.
- `tests/startup/test_journal_name_resolver_wiring.py` (new) -- AST structural guard proving `_phase_gateway` calls `register_task_name_resolver(db_pool)` unconditionally.
- `tests/journeys/test_memory_fix_guards.py` -- autouse reset added so its real `_phase_gateway()` drive can't leak a resolver registration into later tests.

**Review findings breakdown:**
- **Patched (7):** redundant double registry lookup in `record()` (low); duplicated `_MAX_LABEL_LEN` literal (low); missing `# 3. STEP` logging comment (low); missing 4-point logging on six new/changed methods (medium — house rule violation); `narrate()` crashing uncaught on mismatched `attrs` instead of failing loudly (medium); no test proving the boot-time `NameResolver` wiring, so a silent regression would ship undetected (medium); a real test leaking a global resolver registration with no teardown (low).
- **Deferred (1):** `task_events.py`'s pre-existing (Story 2.1) comment says `task.dead_lettered` is "one of the two NAMED" needs_you/high examples; AD-5 now names three. Cosmetic, not caused by this story. Logged to `deferred-work.md`.
- **Rejected (10, reasons recorded in the Review Triage Log above):** no production caller for the narrator yet (false -- intent's own epic sequencing defers delivery-surface wiring to later stories); `locale` parameter that only accepts `"en"` today (false -- exact architecture-mandated signature); `Intensity.NORMAL` unused so far (false -- same accepted forward-looking-registry pattern as Story 2.1's `record_kind`); regex-based (not AST) "constructed only inside `journal/`" tripwire (low, reject -- no file today triggers a false positive, and a robust fix needs more than a direct correction); hypothetical crash on `_phase_gateway` re-entry (false -- never demonstrated reachable); nothing committed yet at audit time (false -- correct mid-workflow state, review precedes commit by design); the spec's own manual live-DB check not performed (low, reject -- no live gateway process exists in this session's environment to drive, and restarting production is explicitly the coordinator's decision, not this session's); bare `ValueError` vs. `DomainError` for two registration-time refusals (false -- matches Story 2.1's own `EventRegistry.register()` precedent); AD-30's command-read-back half uncovered (false -- `CommandContext` doesn't exist until Epic 4); no new `deferred-work.md` entry at audit time (false -- addressed by this same pass's one `defer` entry).

**Follow-up review recommendation: true.** Three medium-severity findings were patched in this single pass (missing 4-point logging across six methods, `narrate()`'s missing attrs-model validation, and the missing boot-wiring test) -- at or above the two-or-more-medium threshold for recommending a fresh pass, even though every patch was independently re-verified by this session (scoped test re-runs, a full repo-wide `-m tripwire` run, `./scripts/tripwires.sh`, and direct `ruff`/`mypy` on every touched file, all green). The specific unverified risk worth a fresh look: the new boot-wiring test (`tests/startup/test_journal_name_resolver_wiring.py`) is a structural AST guard proving the call exists in source, not a live behavioral test proving the resolver is actually exercised against a real `DbPool` at a real boot; and the `narrate()` attrs-validation fix is proven only against a hand-built mismatched `JournalEvent`, not through any real production emitter (none exists yet for `narrate()`, unlike `record()`'s equivalent guard).

**Verification performed:**
- `uv run pytest tests/journal/ tests/pipeline/durable/test_journal_names.py tests/pipeline/durable/test_journal_wiring.py tests/startup/test_journal_name_resolver_wiring.py tests/journeys/test_memory_fix_guards.py -q` (post-patch, independently re-run by this session) -> 56 passed, 0 failed.
- `./scripts/tripwires.sh` (post-patch, independently re-run by this session) -> TRIPWIRES PASS -- 706 passed/2 skipped (`-m tripwire`), B4/B8/B9 boundary checks pass, ruff findings 29 (baseline 35), mypy errors 57 (baseline 65), both unchanged by this diff.
- `uv run ruff check` / `uv run mypy` scoped to every touched file directly (independently re-run by this session): clean, zero new findings.
- Matrix Test Audit: all 9 I/O & Edge-Case Matrix rows covered by at least one test that ran and passed, confirmed by this session.
- Checked for a live gateway/core process to run the spec's manual `scripts/dev_ingress.py` check: none running in this session's environment (`ps aux`, `~/.stackowl/runtime/` both checked) -- correctly left to the coordinator's own deployment decision, per this dispatch's explicit instruction not to restart production from this session.
- Deliberately did **not** restart the live production core/gateway process -- that deployment decision belongs to the coordinator, per this dispatch's explicit instruction.

**Residual risks:** the deferred item above (pre-existing comment inaccuracy, cosmetic); the two structural/hand-built-only verifications named in the follow-up-review paragraph; no gateway push, record readers, retention/pruning, or live delivery-surface wiring exist yet (explicitly out of this story's scope, per `epic-2-context.md`'s own Cross-Story Dependencies -- later stories in Epic 2 and Epic 3).
