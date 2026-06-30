# PA5(b) — Undelivered-outbox seam (silent-delivery gate). Architecture.

## Problem (the silent-fail hole)
Proactive/scheduled output that cannot be delivered NOW is, on several paths,
**dropped (logged only)** — the body is gone:
- transport failed past retry + fallback-reroute — `notifications/deliverer.py:126-159` / `proactive_job.py:175-181` (only a `delivery_attempts state='failed'` dedup row, no body kept).
- `suppressed` (low urgency under hard focus) — `notifications/router.py:294-298` (audit-only `notification_log`, body discarded).
- morning-brief / check-in with no wired deliverer — `scheduler/handlers/morning_brief.py:207-213` (telemetry only).

This violates the arc invariant: uncertainty must fail CLOSED + leave a durable
NACK, never a silent drop.

## NOT in scope (already correct — do not touch)
- Quiet-hours / focus DEFER → `notification_queue` + `NotificationDigestJob` time-push (`router.py:267-289`). That path already persists the body and re-pushes; it is NOT a silent drop.
- F-62 handler-not-registered → job left **pending** (intentional, self-recovers). NOT a NACK.
- Handler-raised-past-retries → `jobs.last_error/failure_count` + `audit_log` (already durable).

## Decision
A **dedicated durable store** `undelivered_outbox`, surfaced on the user's NEXT
interaction as a banner (policy: defer + surface-on-next-contact; next-session
banner). Dedicated (not `notification_queue`) because the lifecycle differs:
persist-until-next-contact-then-clear vs time-scheduled push, and to avoid
coupling/regressing the working digest. Reuse PATTERNS: migration/store shape,
`owner_id` tenant scoping, DELETE/mark-on-clear (mirrors `digest_job` clear).

## Components

### 1. Migration `00NN_undelivered_outbox.sql`
```
CREATE TABLE IF NOT EXISTS undelivered_outbox (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  owner_id     TEXT NOT NULL,          -- tenant scope (DEFAULT_PRINCIPAL_ID single-user)
  identity_key TEXT NOT NULL,          -- WHO to surface to (cross-channel identity)
  channel      TEXT,                   -- channel it was meant for (nullable)
  category     TEXT,
  urgency      TEXT,
  body         TEXT NOT NULL,          -- the undelivered content (NOT a hash)
  reason       TEXT NOT NULL,          -- transport_failed | suppressed | no_deliverer
  job_id       TEXT,                   -- provenance when from a job
  created_at   REAL NOT NULL,
  surfaced_at  REAL                    -- NULL until shown on next contact; set on clear
);
CREATE INDEX IF NOT EXISTS idx_undelivered_pending
  ON undelivered_outbox (owner_id, identity_key) WHERE surfaced_at IS NULL;
```

### 2. Store `notifications/undelivered_outbox.py` (mirror existing store shape)
- `record_undelivered(db, *, identity_key, body, reason, channel, category, urgency, job_id, owner_id=DEFAULT)` — INSERT (the NACK write). Never raises (B5: a NACK-write failure logs, never breaks the caller).
- `list_pending(db, identity_key, owner_id=DEFAULT) -> list[Row]` — `surfaced_at IS NULL`, oldest first, bounded LIMIT.
- `mark_surfaced(db, ids, owner_id=DEFAULT)` — set `surfaced_at` (the clear).
- `pending_count(db, identity_key, owner_id=DEFAULT) -> int`.

### 3. Emission seam — call `record_undelivered` at each silent-drop point
- `deliverer.py` transport-failed terminal (after retry + reroute exhausted) → reason `transport_failed`.
- `router.py` `suppressed` branch → reason `suppressed`.
- `proactive_job.py` / morning_brief undeliverable rollup → reason `no_deliverer`.
ONE shared call; identity_key resolved from the job's target / recipient (reuse
the durable recipient on `jobs.target_addresses` / the proactive recipient
resolver). Effect is ADDITIVE — replaces a drop with a durable row.

### 4. Surfacing — next-contact banner
On an inbound user turn, BEFORE the owl's first response: read `list_pending`
for the turn's `identity_key`; if non-empty, inject a banner
("N things I couldn't deliver while you were away: …") and `mark_surfaced`.
Injection point: a small pre-delivery/assemble hook keyed on identity — reuse
the `assemble.py:184` system-prompt concat OR a dedicated surface step in
`PIPELINE_STEPS`. MUST be idempotent (mark_surfaced clears so it shows once) and
only on a real user turn (not a delegated child / proactive turn). Identity via
the existing cross-channel `identity_key` (see identity unification arc).

### 5. PA5(b) gate (the ratchet — assert on the STORE, never a log)
`tests/notifications/test_undelivered_outbox_gate.py`:
- a proactive delivery whose transport FAILS (fake transport) → assert a durable
  `undelivered_outbox` row exists (read it back) with the body + reason. NOT a log.
- the `suppressed` router path → assert a durable row (body retained, not just `notification_log` hash).
- next-contact surfacing: seed a pending row → drive an inbound turn for that
  identity → assert the banner carries the body AND the row is now `surfaced_at != NULL`
  (shown once; a second turn does not re-surface).
- DISTINCTNESS guard: F-62 pending job and a quiet-hours digest-deferred item do
  NOT create outbox rows (assert absent) — proves we ratchet the RIGHT state.

## Invariant locked
Every proactive/scheduled body that would otherwise be dropped is now a durable
`undelivered_outbox` row (fail CLOSED) and is surfaced exactly once on next
contact — never a silent log line.
