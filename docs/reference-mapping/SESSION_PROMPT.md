# Session kickoff prompt

Copy the block below into a new Claude Code session in this repo. It re-establishes the
working method without needing the previous conversation, and it runs AUTONOMOUSLY — it
does not stop to ask questions.

---

```
/loop /item-loop

Run the reference-mapping programme autonomously. Do not wait for me. I may be asleep;
assume no answer is coming and keep working on everything that is not blocked.

Read these first, in order, before doing anything else:

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

  BRAINSTORM = the same 25 questions, but I do not answer the ones evidence can. Draft
  them, then route each:

    * DERIVABLE — the answer is in the code, the live database,
      ~/.stackowl/logs/stackowl.jsonl, the reference clone, or a decision already in
      progress.yml. The panel answers it. Record the answer AND the evidence. A question
      is only derivable if you can NAME the evidence; "the panel agreed" is not evidence.
    * IRREDUCIBLY MINE — product intent, priority, appetite for risk, whether a
      user-facing thing may change. Do NOT answer it and do NOT guess. Append it to
      `current.ESCALATIONS` with: the question, why evidence cannot settle it, your
      recommendation, and what is blocked until I answer. Then CARRY ON with everything
      it does not block.

  Round 0 is always MEASUREMENT, dispatched before any question is drafted; every other
  lens cites its numbers. Run the panel as parallel subagents in one message —
  Measurement, Stability, Improvement, Killer-functionality, Ease-of-use, Future-proof —
  each returning a position, its evidence, and its objection to the others. Where two
  lenses disagree, that disagreement IS the finding: put it in the record rather than
  averaging it away.

  DO NOT USE AskUserQuestion. There is nobody at the keyboard. An escalation queued in
  progress.yml is how you ask; I clear them in one sitting.

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
    A red test that appears after your change is usually telling you the change is
    incomplete, not that the test is wrong (2026-08-22: three of them revealed that the
    ownership rule lives in FOUR functions and I had patched the one not on the path).
  - The honesty machinery is usually RIGHT. When the overclaim gate names a failed
    capability, the first question is whether the TOOL lied to it — twice on 2026-08-22
    `owl_build` reported success on a write that never landed, and once its verifier was
    blind to the very fields its own tool writes. Suspect the reporter last.
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

STOP AND ESCALATE — do not decide these alone. Write the brief into
`current.ESCALATIONS` and move to work that is not blocked:

  1. Removing or disabling anything user-facing.
  2. A destructive migration, or any data deletion.
  3. Editing a code block shared with consent or clarify while its smoke suite is red.
  4. Three failed fix attempts on one problem — that is an architecture question, not a
     fourth attempt.
  5. A panel answer that contradicts a decision already recorded in progress.yml.

RUNNING UNATTENDED — the rules that only matter when nobody is watching:

  - ruff and mypy baselines are 37 and 65 in src/. Check BOTH before every commit;
    neither may rise. If your change raises one, that is the change's problem to fix.
  - Real suite durations on this box: tests/providers ~185s, tests/pipeline ~740s,
    tests/tools ~990s, tests/tools/meta ~250s, tests/scheduler ~1200s. Under-budgeting
    produces exit 143, which is SIGTERM from your own timeout and NOT a red test — never
    report it as one. Run the whole directory suite before claiming green.
  - NEVER write a claim into progress.yml or a document you did not measure. A stage you
    could not evidence is `blocked` or `partial` with the reason — never `done`.
  - progress.yml is edited by careful surgical scripts ONLY, bounded to one item. Diff
    every OTHER item before and after and assert nothing else changed, then run
    scripts/progress_lint.py.
  - Restart only via ./start.sh, verify via the JSONL log, and NEVER while I have a turn
    in flight. The core self-restarts ~35s after a .py edit; check its boot time against
    your last commit before believing any measurement.
  - If a log line is the evidence for a claim it must be INFO, not DEBUG — production
    runs at INFO. Run the query that would CLOSE an acceptance check before you need it.
  - Everything ships ENABLED. Never ask me to turn a capability on; an operator switch is
    an opt-out only.
  - Scope your counts and say which you are quoting: "last 3 days" and "all-time" answer
    different questions and have embarrassed this programme before.

Start by stating what progress.yml `current` says and what you are picking up, then work
the item to done without checking in. Report when the item is closed, when you have
queued escalations, or when you are genuinely blocked on all fronts.
```

---

## What `current` says right now (2026-08-22)

- **Item:** `D05.4` — progressive tool disclosure, **stage `validate`**, at
  `validate:partial / document:partial`. It cannot reach `done` without a shape of
  traffic only real use produces: a multi-turn interactive lane whose memo is
  invalidated mid-conversation. Three of its four acceptance checks WAIT ON TRAFFIC and
  none is claimed.
- **The map:** 112 items — 2 CONFLICT, 43 MISSING, 29 PARTIAL, 6 DIVERGENT, 15 PARITY,
  16 AHEAD, 1 NATIVE. Completion is tracked narratively in `current`, NOT by a status
  field on the items — do not quote a "N of 112 done" figure, there isn't one.
- **Escalations:** 28 recorded, 5 with no `RESOLVED` key. `ESC-34` is the newest (a
  blocked owl was shown an escape hatch that does not open).
- **Waiting on Bakir:** `N01 · Dreaming` (his own idea, native `N` namespace, the
  DreamWorker handler is registered and UNSCHEDULED as its seat), and the **LanceDB
  removal arc** — decided 2026-08-14, not started.

### What happened on 2026-08-22 — a platform-defect day, not a programme day

The programme did not advance. A full day went to live failures Bakir hit in Telegram,
all of them measured from the log and the database rather than reasoned about. Read the
commits; each message carries its own evidence. In rough order:

- **log retention was configured and could never fire** — `TimedRotatingFileHandler`'s
  deletion searches for `stackowl.jsonl.DATE` while the custom namer writes
  `stackowl-DATE.jsonl`. 772 MB across 31 files, `backupCount=30`, 0 ever deleted. Now
  7 days and it works.
- **one shared Python env** — four virtualenvs in the workspace (707 MB, two
  byte-identical). The learning loop had recorded env-CREATION as a winning lesson, so
  the platform was teaching itself to sprawl. `StackowlHome.python_env()` +
  `WorkspaceEnvJanitor` + the two lessons rewritten.
- **capability gaps now self-heal** — 85 bounds refusals in three days reached nobody,
  because the refusal was recorded only into a per-TURN ContextVar. It is now durable in
  `audit_log`, and a gap INSIDE the owl's creation ceiling is granted with no human at
  all (the ceiling IS the operator's standing grant); only ceiling-crossing escalates.
- **`owl_build` grant read the CEILING, not `bounds ∩ ceiling`** — so a tool in the
  ceiling and missing from bounds reported "already allowed — nothing to grant",
  returned success and wrote nothing.
- **the edit verifier was blind to `lifecycle` and `schedule`** — the two fields you
  would actually edit on a scheduled owl. Zero checked fields → UNKNOWN → the overclaim
  gate default-denies → a landed edit reported as a failure.
- **"Brain?" became a work order** — the vocative strip removes `[\s,:;]` but not `?`,
  so the goal was literally `?`. Given no instruction the agent found an old
  conversation in memory and acted on it for 266 seconds. Now a remainder with no letter
  or digit in any script routes the original text.
- **the secretary is ROOT** (Bakir's decision) — she was already unbounded; what stopped
  her were the ownership rule and the task envelope, neither of which consults bounds.
  `ROOT_OWL` / `is_root_owl` in `owls/tool_presets.py`, honoured by all four gates.

**Two open items from that day, neither proven:** `secretary` editing `syshealth`
without the ownership refusal, and `sysdesign`'s daily `web_search` refusal closing
itself on the next 6-hourly sweep. Both should now happen unattended — if they do not,
the fixes are not real. Also unexplained: a 20-step budget cap that truncated a turn
mid-work, and the fact that `secretary` is the FALLBACK caller, so unattributed calls
now carry root authority.
