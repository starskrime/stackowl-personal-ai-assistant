# UniOwl — Full Product Implementation Plan

Spec: [[UNIOWL_ARCHITECTURE.md]]. Decision: build the FULL product (all 4 ADRs), no MVP cut.
Method: subagent-driven, story-granular commits, QA+dev review each change, gateway integration test per story. Reuse-before-build. All flags ship ON when arc complete.

## Resolved founder decisions (defaults — no deferrals)
1. **Identity scope**: display_name unique **per-user**; system `name` slug globally unique. NL routing gated **per-channel**.
2. **Always-on**: min interval floor **5 min** (sub-5 needs explicit override); per-user quota **5 scheduled owls** (reuse `MAX_AGENT_OWLS` pattern). Jetson-safe.
3. **Failure policy**: retry-once → on 3 consecutive fails **circuit-break → pause owl's job + ONE notification** (reuse B4 recovery ladder / existing circuit breaker). Never silent, never infinite.
4. **Retire** = **soft-disable** (recoverable; jobs torn down transactionally) by default; hard-delete requires explicit `YES` confirm (mirror `/owls remove`).
5. **Group-chat routing**: multi-party channels = `@name` or owl-owned-name only; full vocative NL routing only in 1:1. Prevents human-name collision.
6. **Authority ceiling**: chat-minted owls stay under `SAFE_DEFAULT_CEILING` (already enforced by `build_agent_manifest`); confirmed, no change.

## Build sequence (each = implement → QA+dev review → test → commit)

### Epic 1 — Data model (foundation for everything)
- **S1. Manifest fields**: add `display_name: str`, `lifecycle: Literal["on_demand","scheduled"]="on_demand"`, `trigger: TriggerSpec|None` to `OwlAgentManifest`. New `TriggerSpec` discriminated union (`cron`|`watch`|`threshold`). YAML round-trip (`manifest_to_yaml_entry` + `from_settings`). display_name→name slugify helper. Back-compat: defaults make existing owls byte-identical.
- **S2. Uniqueness**: per-user display_name uniqueness check (edit-distance ≥2) reusing `FuzzyMatcher`; slug global-unique (exists).

### Epic 2 — ADR-A: Resumable validator-gated creation
- **S3. MissingFields**: validator returns complete owl OR `MissingFields(fields, partial)` instead of erroring on underspecified spec.
- **S4. ClarifyGateway wiring**: `owl_build` raises `ClarifyRequest` carrying the partial manifest in the resume envelope; merge answer → re-validate → loop → mint via existing `_create`. Schema decides *whether* to ask; LLM phrases. Draft-save on abandon; ephemeral until confirmed.
- **S5. Goal→type inference**: infer preset/specialty/persona from the goal sentence (LLM, fast tier) so the user is never asked "what type". Name suggestion + reroll.
- **S6. Entry points**: `/agent` command (alias to owl creation flow) + `/owl`; NL "create an agent that…" detected → creation flow. First-contact discovery nudge.

### Epic 3 — ADR-D: Vocative-position routing
- **S7. Scanner rule**: in `GatewayScanner.scan`, before secretary fallback, match registered display_name in vocative position (turn-initial/terminal), Unicode `\p{L}`/NFC/casefold/RTL-aware, greeting-independent (NO hardcoded "Hey"). Fail-safe to secretary; disambiguate close candidates via ClarifyTool. Per-channel gate (decision 5).
- **S8. Resolver casefold**: `resolver.resolve_target` casefold + display_name awareness for delegation consistency.

### Epic 4 — ADR-B: Lifecycle projection
- **S9. Reconcile loop**: manifest=truth, scheduler rows=projection. On boot (extend `from_settings`) + on create/retire/edit: idempotent-upsert owl-owned scheduler row for `lifecycle=scheduled`; delete owned rows for gone/on_demand/retired owls. Provenance marker; never touch hand-made cronjobs. Jobs key on stable id; re-read spec at fire; no missed-run backfill.
- **S10. Transactional retire**: retire tears down owned jobs in same op (decision 4).
- **S11. Guardrails**: interval floor, per-user scheduled quota, cost ceiling per-agent+global (reuse BudgetGovernor), notification budget, circuit-break+1-notify on repeated failure (decision 2,3).

> **S9–S11 status (shipped):** Reconcile loop in `src/stackowl/scheduler/owl_lifecycle.py` (manifest=truth → idempotent owned-row projection, provenance marker `params.source='owl_lifecycle'`+`owner=<name>`; deterministic job_id `owl_lifecycle-<name>`; cron→goal_execution, watch→website_watch). Called at boot (orchestrator, in the core-owns-scheduler block after `recover()`) and after every owl create/edit/retire (`owl_build._reconcile_schedules`) + `/owls remove`. Guards in `src/stackowl/owls/owl_schedule_guards.py`: interval floor 5 min (REFUSED at manifest validation — single source of truth — not clamped), scheduled-owl quota 5 (defensively capped at projection too), 3-consecutive-failure circuit-break → pause + ONE alert (`scheduler._mark_failed`, scoped to owl jobs by marker; `failure_count` now means consecutive-since-success). **threshold** triggers are SKIPPED in the projection (no handler until S12/S13) — logged, never crashes. **TODO (daily-spend cap):** per-agent/global daily cost ceiling NOT yet wired — interval floor + quota + failure-pause bound runaway cost for now; wire `BudgetGovernor` per-agent daily cap in a follow-up (the per-turn BudgetGovernor already bounds single-run cost).

### Epic 5 — ADR-C: Conditional/threshold monitor
- **S12. ThresholdWatchHandler**: model on `WebsiteWatchHandler`; generic numeric source (tool/URL+extractor, NO vendor/market SDK, no `btc` literals) + predicate `(op, threshold)` + edge-trigger + hysteresis (last_state, fire false→true, re-arm on cross-back/cooldown).
- **S13. Wire trigger=threshold** into the reconcile loop → ThresholdWatchHandler.

### Epic 6 — Visibility + close
- **S14. Roster view**: `/agents` (+`/owls`) roster — display_name, what it does, lifecycle status (🟢active/💤resting), manage buttons. No IDs/system names shown.
- **S15. Live E2E**: create on-demand owl by chat → talk by name; create scheduled threshold owl → fires on condition; retire → job gone. Real backend. Flags ON. Merge to main.

## Verification (every story)
Unit + gateway integration (mock only provider) + targeted suite (no full pytest — Jetson hangs). Live E2E at S15. Keep boot green (`reachability_enforcement: block`, `acceptance_authority: true`).
