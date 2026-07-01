# Task PB7a: Wire the undelivered_outbox safety net into its 3 silent-drop seams + next-contact banner

## Status: IN PROGRESS — continue, do not restart
Two files already exist in the working tree, already correct, DO NOT rewrite them, only add tests if gaps found:
- `src/stackowl/db/migrations/0073_undelivered_outbox.sql` — the table + partial index. Done.
- `src/stackowl/notifications/undelivered_outbox.py` — `UndeliveredOutbox` class (lines 73+) with the store API,
  and a `render_banner()` function (line 272+). Read this file first to learn the exact method signatures before
  writing any calling code — do not guess signatures.

## What's NOT done yet (your job)
Read the full spec at `docs_archive_ralph_2026-06-30/PA5B_DESIGN.md` sections 3, 4, and 5 — it is the canonical,
already-approved design. Do not redesign; implement exactly what it specifies.

1. **Wire `record_undelivered` at the 3 silent-drop seams** (section 3 of the spec):
   - `src/stackowl/notifications/deliverer.py` — the terminal `"failed"` branch (after retry + fallback-reroute
     exhausted). This is around where the deliverer currently returns `"failed"` to its caller without persisting
     the body anywhere — find that exact return point.
   - `src/stackowl/notifications/router.py` — the `suppressed` branch (~line 294-298 per prior research, verify
     current line numbers, code may have shifted).
   - `src/stackowl/scheduler/handlers/morning_brief.py` — the no-deliverer / `"undeliverable"` rollup branch
     (~line 207-213 per prior research, verify current line numbers).
   - Resolve `identity_key` the same way these call sites already resolve their delivery target (reuse the
     existing recipient/identity resolution — do not invent a new lookup).
   - Each call is ADDITIVE: it replaces a silent drop with a durable row, it does not change existing control
     flow, return values, or logging.

2. **Surface pending rows as a banner on the next real inbound user turn** (section 4 of the spec):
   - Read `list_pending` for the turn's `identity_key` BEFORE the owl's first response is generated.
   - If non-empty: inject the banner text (`render_banner()` already builds this) into the turn — the spec
     names the injection point as either the existing `assemble.py:184`-area system-prompt concat, or a
     dedicated step in `PIPELINE_STEPS`. Pick whichever the current codebase structure makes cleaner; note your
     choice in the report.
   - Call `mark_surfaced` for those rows so the banner shows exactly once (a second turn does not re-surface).
   - MUST be idempotent and MUST only fire on a real user turn — not a delegated child turn, not a proactive/
     scheduled turn. Find how the pipeline currently distinguishes turn kinds and gate on that.

3. **Tests** — `tests/notifications/test_undelivered_outbox_gate.py` per spec section 5, all 4 assertions:
   - A proactive delivery whose transport FAILS (fake transport) → assert a durable `undelivered_outbox` row
     exists (read it back from the DB) with the body + reason. NOT a log assertion — a DB read-back.
   - The `suppressed` router path → assert a durable row (body retained, not just a `notification_log` hash).
   - Next-contact surfacing: seed a pending row → drive an inbound turn for that identity → assert the banner
     text appears AND the row's `surfaced_at` is now non-NULL (shown once; a second turn does not re-surface).
   - DISTINCTNESS guard: an F-62 pending job and a quiet-hours digest-deferred item do NOT create outbox rows
     (assert absent) — this proves the seams only fire on the 3 genuine silent-drop paths, not on paths that
     already have their own correct recovery (quiet-hours defer, pending-job self-recovery).

## Global constraints (binding, from the project's standing conventions)
- Writes to the outbox are best-effort: a NACK-write failure must log but NEVER raise/break the caller (the
  store module's docstring already states this — honor it in your wiring, do not add exception handling that
  could turn a write failure into a crash of the already-failing proactive path).
- 4-point logging (entry/decision/step/exit) on any new code path you add, matching this repo's existing
  logging convention (see CLAUDE.md "Per-Tool 4-Point Logging Standard" — same pattern applies beyond just
  tools, to any execute-shaped method).
- Minimal diff. You are wiring 3 call sites + 1 surfacing hook — do not refactor the surrounding functions
  beyond what's needed to add the call.
- Targeted tests only + `uv run ruff check` + `uv run mypy` on changed files. NEVER run the full pytest suite
  (it hangs on this box per project convention) — use targeted paths.
