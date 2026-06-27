# ADR-4 — Reachability invariant: registration is incomplete until reachability is proven at boot

- **Status:** Proposed
- **Theme:** T4. Closes/strengthens F-45, F-76, F-77, F-78, F-86; ⤷F-87.
- **Depends on:** none. Enables ADR-6 (its lifecycle variant).

## Context
Capabilities ship as dangling half-edges (heuristic store with no caller F-45; `/urgent` that never
transports F-76; an unseeded digest job F-77; an empty-allow-list event bridge F-78) because tests
assert *registered* but nothing asserts *reachable*. The fail-closed reachability census already exists
(`health/reachability/census.py:62` `run_census`, `:85` `census_passes`) but `StartupOrchestrator.run`
never calls it — the very tool that would catch the bug is itself an instance of the bug (F-86).
Directives: nothing removed (the census is *run*, not rebuilt); this is the codebase's most-repeated
root ("registered≠reachable").

## Decision
Make **reachability a boot invariant**: `StartupOrchestrator.run` gains a **reachability phase** that
runs `run_census()` over every registered capability (handlers, tools, jobs, channels, skills) and
**refuses READY / emits a loud degraded alert** on `census_passes()==False`. A capability is "registered"
only once a probe proves it is reached on the live path. New capabilities must register a probe (a
`RegistrationContract` enforced at assembly time), so a half-edge cannot ship green.

## Why this, not the alternatives
1. *Add a wiring-audit test per feature.* Rejected: that's what exists and what keeps missing cases;
   reachability must be asserted *generically at boot*, not per-feature-by-memory.
2. *Run the census only in CI.* Rejected: the dead edges in the audit passed CI; reachability must hold
   on the *live* boot path (the census is the "Live Path Census law" from memory — make it executable).
No latency concern — a powerful machine runs the census at boot cheaply.

## Shape
- `run_census()` becomes a boot phase; `census_passes` gates READY (or emits a critical alert if you
  prefer warn-not-block — choose block in production once stable).
- `RegistrationContract`: registering a handler/tool/job *requires* declaring how it's reached (a probe
  or a seeded edge). Subsumes by delegation: `scheduler/assembly.py`, `commands/assembly.py`,
  `notifications/assembly.py` registration sites gain the contract; the census *consumes* their probes.
  Closes F-45 (heuristic store must declare a live reader), F-76 (/urgent must declare a transport),
  F-77 (digest job seed), F-78 (event bridge publisher).

## Invariant established
**Every registered capability is provably reached on the live path before the platform reports READY.**
A dangling half-edge fails the boot, not the user.

## Migration plan (flag-gated; default ON once verified)
1. Wire `run_census()` as a boot phase in warn-only mode → off/warn = byte-identical boot.
2. Add probes for the known dead edges (F-45/76/77/78), turning each green.
3. Flip the phase to block-READY; add `RegistrationContract` so new edges can't regress.

## Verification
- A boot test that registers a deliberately-dangling handler and asserts the census fails READY.
- Live: boot with the digest job unseeded → boot refuses/alerts instead of silently shipping it dead.

## Blast radius, risk, rollback
Boot-path; flag-gated (warn→block). Risk: a false-unreachable verdict blocks boot (mitigated: warn-mode
burn-in before block). Rollback: flag to warn-only.

## Effort & dependencies
**M.** Independent; do early — it prevents every future half-edge, including those introduced by other ADRs.
