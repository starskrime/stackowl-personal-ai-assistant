# Session kickoff prompt

Copy the block below into a new Claude Code session in this repo. It re-establishes the
working method without needing the previous conversation.

---

```
We are rebuilding StackOwl against a reference architecture. Read these first, in order,
before doing anything else:

  1. progress.yml                            — state of record. `current` says where we are
                                               and carries the lessons that cost the most.
  2. docs/reference-mapping/PROCESS.md       — how the work is done. This is the method.
  3. docs/reference-mapping/DOC_STANDARD.md  — what every document must contain.

The reference platform is cloned READ-ONLY at do_not_push_to_git_research_only/ —
gitignored, never push it. Where it and StackOwl disagree, assume it is right until
evidence says otherwise; but 16 mapped items are ones where StackOwl is genuinely ahead,
so that default is rebuttable. Port the DESIGN, never the code: read their implementation
until you can explain it, write ours in our idiom, then state the divergence in the doc.

NO VENDOR NAMES IN src/, tests/ OR scripts/. Say "the reference platform". The name
belongs in progress.yml and docs/ — the research record — and nowhere else.

USE SKILLS. Not only when they obviously apply:
  - superpowers:brainstorming                   before any design work
  - superpowers:writing-plans                   decisions -> implementation plan
  - superpowers:test-driven-development         tests before implementation
  - superpowers:systematic-debugging            any bug, before proposing a fix
  - superpowers:verification-before-completion  before claiming anything is done
  - superpowers:requesting-code-review          before merging
  - tonyStyle                                   every task touching StackOwl code
  - graphify query "<question>"                 codebase questions, BEFORE grepping
Announce which skill you are using and follow it.

HOW WE WORK, per item:

  Seven stages, none skipped: brainstorm -> architect -> implement -> cleanup -> test ->
  validate -> document. `no_change_needed` is valid; silence is not. Update progress.yml
  after EVERY stage, and run `uv run python scripts/progress_lint.py` after every edit to
  it — duplicate keys silently swallow whole records, which has happened.

  Before building anything, run `uv run python scripts/map_check.py "<what you are about
  to build>"`. An item was once designed, built and shipped before anyone noticed it was
  already mapped.

  BRAINSTORM = 25 questions, batched 4 per round via AskUserQuestion, each with your
  recommended answer. Ask the ones whose answers change what you build. FIND FACTS
  YOURSELF — never ask me something you could measure. Hunt contradictions in my answers
  and put them back to me; do not silently reconcile them. /grill-me runs this as a
  design-tree interview when the shape is unclear.

  MEASURE BEFORE YOU ARGUE. Every significant call this programme got right came from a
  number off the live database or the JSONL log, and most of the wrong ones came from
  reasoning about code instead. Do not describe a store as healthy, a loop as running or
  a feature as live without a count.

STANDING RULES (full list in progress.yml `rules`):
  - Check existing code before writing new. Wire and extend, never recreate.
  - Minimal diffs. Fix the architecture, not the example that surfaced it.
  - No hidden errors — every catch logs. 4-point logging on every new execute().
  - No vendor-specific logic: dispatch on shape and capability, never a provider name.
  - No hardcoded English word lists. If a rule needs one, the real signal is a SHAPE.
  - Schema changes are idempotent migrations only. The runner wraps each in a
    transaction — VACUUM inside one fails the whole migration.
  - Never disable or remove a user-facing feature without asking, even when the backing
    store is gone.
  - Finished features ship ON, not dormant behind a flag.
  - Targeted test paths with timeouts — never a full pytest run, it hangs on this box.
    A HANGING TEST IS A FAILING TEST; do not file it as slowness.
  - Restart with ./start.sh and verify via ~/.stackowl/logs/stackowl.jsonl, not a PID.
    A DELETION IS NOT LIVE UNTIL THE PROCESS HOLDING THE OLD CODE IS GONE.
  - Pre-existing red is not out of scope. Root-cause it, fix it, and say so.
  - Never autonomously "fix" a failing test to make your change pass — stop and tell me.
  - Commit at sub-story granularity when green. Merge to main and push when done.

FOUR FAILURE MODES THIS PROGRAMME KEEPS FINDING — check for them by default:

  1. A WRITE WITH NO READER, or an actuator wired on only some paths. A reader with no
     writer; an FTS leg never wired so archived skills still ranked; a retry_at that
     added a trigger instead of a delay. Measure the EFFECT, never trust the call.
  2. TEST DOUBLES THAT STOPPED RESEMBLING THE REAL THING — seven instances in one arc.
     Where you can, GENERATE fixtures from the same constants the code uses.
  3. TWO COPIES OF ONE RULE. A regex in three files; a subcommand list in a command and
     its meta test; a table's DDL hand-rolled in two test files that had already drifted.
     One source, and have the other ask it.
  4. NO DECAY. Anything that only ever appends will reach 100k rows and poison whatever
     reads it. When you remove a writer, ask what was BOUNDING the thing it wrote to.

Start by telling me what progress.yml `current` says, then continue from there.
```

---

## What `current` says right now (2026-08-10)

- **Item:** `D08.1` — two-file curated memory. All four closers landed; live.
- **Shipped this arc:** `D09.3` (skill curator + consolidation, catalog 437 → 168),
  `D10.2` (authoring standard enforced at the write, 154/154 skills migrated),
  `D08.1` slices 1–4 + the removal, `D08.3` (absorbed as the nudge).
- **Open acceptance check:** that the assembled prompt demonstrably carries the two
  memory files — needs a real turn, deliberately not asserted from the code path.
- **Next:** `D08.2` (raised to P1) — the `MemoryBridge` split plus the three channel
  approve/reject callbacks, in one careful pass on the orchestrator block they share
  with consent and clarify.
- **Waiting on data:** whether `lessons.lance` (221 MB) earns its place. `n_hits` now
  logs at INFO; one day of traffic settles it.
- **Waiting on design:** `N01 · Dreaming` — first item in the native `N` namespace.
