# ADR-6 — Closed-loop lifecycle: detect → heal → verify, instead of detect-only

- **Status:** Implemented [x] (2026-06-27) — all 7 findings closed; flag `health_loop` default OFF (owner-held, not yet flipped)
- **Theme:** T6. Closes/strengthens F-36, F-39, F-73, F-74, F-85, F-87, F-88.
- **Depends on:** ADR-1 (verify the heal) + ADR-2 (the heal action) + ADR-4 (reachability of resources).

## Context
All the lifecycle pieces exist *unconnected*: `HealthAggregator` + `ResilienceContributor` +
`attempt_with_recycle` (heal primitive) + the reachability census + `watchdog` + `supervisor`. But
health is a read-only dashboard run only by the `stackowl health` CLI; the watchdog pings on a blind
timer (F-85); the supervisor restarts without progress (F-73/74); a respawned core isn't reconnect-
verified (F-36); a crash is silent to the user (F-39); mono has no supervision (F-88). Nothing turns
"down" into "healed." Directives: nothing removed (the pieces are *connected*, not rebuilt).

## Decision
Introduce one **`HealthLoop`** — an in-process, periodic closed loop: **detect** (`HealthAggregator.
collect`) → **heal** (hand the degraded resource to the ADR-2 `RecoveryActuator` via the existing
`attempt_with_recycle`/`ResilienceContributor`) → **verify** (re-collect; ADR-1-style observation that
the resource recovered) → **escalate** (proactive operator alert + user notice) if heal fails. The
watchdog's `WATCHDOG=1` ping is gated on the loop's liveness verdict (F-85); the supervisor's restart is
gated on a progress/verify signal (F-73/74/36); crashes emit a user-visible notice (F-39); the loop runs
in every role including mono (F-88).

## Why this, not the alternatives
1. *Wire each piece ad-hoc (the S2/S3 partial fixes).* Rejected: that shipped detect+alert but left
   auto-recycle deferred (F-87) precisely because there was no loop to own it.
2. *Rely on systemd/external supervision.* Rejected: it may be absent (F-88), it can't heal a
   deadlocked-but-alive process (F-85), and it can't re-verify a subsystem.
Powerful machine: an always-on health sweep + active heal is affordable.

## Shape
- `HealthLoop` = a seeded scheduler job (the S2 health-sweep seed generalized) wiring
  `HealthAggregator` (detect) → `RecoveryActuator` (heal, ADR-2) → re-collect (verify) → proactive
  alert (escalate). Subsumes by delegation: `watchdog` reads the loop's liveness; `supervisor` reads its
  verify signal; the census (ADR-4) is the boot-time instance of the same detect step.
- `attempt_with_recycle` + `ResilienceContributor` become the loop's heal primitives (already exist,
  currently unwired).

## Invariant established
**A degraded subsystem is acted on and re-verified, not merely reported; the platform never reports
"alive" while a critical subsystem is down.** Detection without remediation is no longer possible.

## Migration plan (flag-gated; default ON once verified)
1. Land the loop in detect+alert mode (already partially shipped) → off = identical.
2. Wire the heal step to ADR-2 `attempt_with_recycle`; gate the watchdog ping + supervisor restart on
   the verify signal.
3. Run in mono role; add the crash user-notice.

## Verification
- Inject a down resource; assert the loop recycles it and re-verifies recovery (or escalates).
- Deadlock a subsystem while the event loop spins; assert the watchdog withholds `WATCHDOG=1` →
  systemd restarts.
- Live: the provider-box-down incident self-heals/alerts instead of needing a human to notice.

## Blast radius, risk, rollback
Lifecycle layer; flag-gated. Risk: a heal loop that flaps (mitigated: ADR-2 bounded attempts +
backoff + escalate). Rollback: flag to detect-only.

## Effort & dependencies
**L.** After ADR-1/2/4. It is ADR-4 (reachability) + ADR-2 (recovery) applied to the lifecycle layer.
