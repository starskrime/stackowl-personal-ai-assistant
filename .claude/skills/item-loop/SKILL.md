---
name: item-loop
description: Drive one reference-mapping item through all seven stages autonomously, with a six-lens panel answering the brainstorm instead of the operator. Use for "work the next item", "continue the programme", or from /loop.
argument-hint: "An item ID (e.g. D08.2), or nothing to take the one in progress.yml `current`"
---

Work **one** item to done. Never two. The programme's state of record is `progress.yml`;
its method is `docs/reference-mapping/PROCESS.md`; every document obeys
`docs/reference-mapping/DOC_STANDARD.md`. Read `current` before anything else.

## The rule above all others — WHY, not WHAT

**Bakir, 2026-08-31, mandatory and outranking every other rule in this skill:
always fix WHY it happened, never WHAT happened.**

Every item, every incident report, every acceptance failure. The symptom is
EVIDENCE OF A CAUSE, not the work item. Repairing the reported thing and filing
its cause as an escalation is the exact move he rejected — escalate a DECISION
only he can make, never the DIAGNOSIS.

An item is not done until you have written down *what made this possible, and
what else does that same cause reach*. If that answer only restates the symptom,
keep going: you have not found the root cause yet.

## Retired means deleted

**Bakir, 2026-09-01: whatever is retired is DELETED — code, registration, tests,
job rows **and the design document's claim about it**, in the same change. Never a
dead seat, never empty scaffolding "for later".**

**The document is the fifth surface and was missing from this list until
2026-09-07.** `d8b8ba81` retired the tool-loop hard stops carefully — code, config
fields, tests, module docstring — and D05.7 advertised the deleted
`hard_stop_enabled` flag for eight days afterwards. Eleven of nineteen stale
documents were made stale by a DELETION; `doc_check.py` now names them separately,
and a deletion-stale document is the one to read first. If you find dead code while working an item, delete it in that
item rather than filing it as debt. Measure that it is unreferenced first, and
remove the WRITER, not just the rows.

## The one rule that makes this safe

**Never write a claim into `progress.yml` or a document that you did not measure.**
An autonomous loop that marks things done is only as trustworthy as its evidence. A stage
you could not evidence is `blocked` or `partial` with the reason — never `done`.

## Before building anything

```bash
uv run python scripts/map_check.py "<what you are about to build>"
```

Matches → you are on a mapped item, run its seven stages. No match → evidence-led work,
record it in `known_debt` with what you knew and what you chose.

## Stage 1 — brainstorm, by panel

The 25 questions still get asked. The operator does not answer the ones that evidence can.

**Round 0 — measure first, always.** Before drafting a single question, dispatch the
Measurement lens. Everything the other five say must stand on its numbers.

Then draft the questions and route each one:

- **Derivable** — the answer is in the code, the live database, `~/.stackowl/logs/stackowl.jsonl`,
  the reference platform at `do_not_push_to_git_research_only/`, or an existing decision in
  `progress.yml`. The panel answers it. Record the answer AND the evidence.
- **Irreducibly the operator's** — product intent, priority, appetite for risk, whether a
  user-facing thing may change. The panel does NOT answer it. Queue it (below) and keep
  working on everything it does not block.

A question is only derivable if you can name the evidence. "The panel agreed" is not evidence.

### The panel

Dispatch as parallel subagents in one message. Each returns a position, its evidence, and
its objection to the others.

| Lens | Its only question |
|---|---|
| **Measurement** | What do the numbers actually say? Query the DB, count the log, read the tree. Runs FIRST; the rest cite it. |
| **Stability** | What breaks, what regresses, what degrades silently? What notices if it does? |
| **Improvement** | Is this actually better than what exists, or just different? |
| **Killer functionality** | Does this make the platform meaningfully more capable, or is it housekeeping? |
| **Ease of use** | What does the operator have to know or do? What gets simpler? |
| **Future-proof** | What does this commit us to? What is expensive to reverse later? |

**Hunt contradictions.** Where two lenses disagree, that disagreement IS the finding — put
it in the record rather than averaging it away. Where a panel answer contradicts an earlier
decision in `progress.yml`, escalate it; do not silently reconcile.

### The escalation queue

Append to `current.ESCALATIONS` in `progress.yml`, each entry carrying: the question, why
evidence cannot settle it, the panel's recommendation, what is blocked until it is answered,
and a **`premise_check`** — a one-liner printing `HOLDS` or `EXPIRED`. Run
`uv run python scripts/escalation_check.py` at the start of a loop and close what expired.

**Why the check is mandatory.** An item gets seven stages and a closing query; an escalation
got written once and never re-read, so its premise aged silently. Measured 2026-09-02: 31
were open and SIX were already settled — two by later work of mine that did not think to
close them, two that expired on their own (decay took `scout.md` back under budget; the 92
armed rollover jobs fired and went terminal), one answered and shipped, one that had said
RESOLVED in its own key since it was written. The queue said 31 when it was 25, and a
question that is no longer a question still costs him the time to decide it is not one.

If you cannot write the check, the premise is too vague to verify — fix the premise.

The operator clears these in one sitting. **Continue the item on everything not blocked.**

## Stages 2–7

Run `PROCESS.md`'s stages in order. `no_change_needed` is a valid outcome; silence is not.
**Update `progress.yml` after EVERY stage**, then:

```bash
uv run python scripts/progress_lint.py
```

Duplicate keys silently swallow whole records. This has already happened.

- **architect** — opens the document. Names the ladder rung, every file it will touch, and
  how Laws 1 and 2 hold.
- **implement** — tests first. Minimal root-cause diffs. Ships ON, not behind a flag.
- **cleanup** — resolve the item's `dedup_target`. `ruff` and `mypy` baselines may not rise.
- **test** — targeted paths with timeouts, **then `./scripts/tripwires.sh` before any
  commit, whatever the item touched. CHAIN IT: `./scripts/tripwires.sh && git commit …`,
  never the gate and the commit as two independent commands in one step.** Measured
  2026-09-02: the gate ran, printed `TRIPWIRES FAILED — do not commit` for a genuine
  unscoped `skills` read, and the commit went out anyway because `git commit` was
  chained to `git add`, not to the gate. A verdict nothing depends on is not a gate.**

  **AND NEVER PIPE THE GATE.** `./scripts/tripwires.sh | tail -6 && git commit` looks
  chained and is not: `&&` binds to the exit status of the PIPELINE, which is `tail`'s,
  and `tail` always succeeds. Measured 2026-09-05 — the gate printed `TRIPWIRES FAILED
  — do not commit` and the commit went out anyway, the SAME defect as above wearing a
  different disguise, three days after the first one was recorded here. Run the gate
  bare, or redirect to a file and chain on `$?`. A pipe is a second way to decouple the
  verdict from the action, and the rule is the verdict must gate the commit by
  construction, not by reading. Targeted paths are chosen by what the change
  looks related to, and a CROSS-CUTTING guard never looks related to anything — which
  is how an unscoped `task_outcomes` read and three stale allowlist entries both
  shipped. The gate takes **~2 minutes** (MEASURED 2026-09-06: 133s wall,
  `138 passed, 2 skipped` in 105.88s — it was ~40s when there were far fewer
  guards, and a stale duration is why a gate gets skipped) and runs everything
  marked `@pytest.mark.tripwire`
  plus `progress_lint` and both baselines.

  **The full run does NOT hang — it takes ~30 minutes, and this line used to say the
  opposite.** MEASURED every time since: `6 failed, 11440 passed in 1885.47s`
  (2026-09-01), `10 failed, 11853 passed in 1775.99s` (2026-09-03), then GREEN three
  runs running — `12123 passed, 0 failed in 1880.78s` (2026-09-05) and `12170 passed,
  18 skipped, 0 failed in 1911.85s` (2026-09-06). Sixth green 2026-09-06: `12284 passed, 18 skipped, 0 failed in 1979.40s` (rc=0), `SUITE TREE STILL`. Seventh 2026-09-06: `12312 passed, 17 skipped, 0 failed in 2146.47s` (rc=0) — the skip count FELL because an unconditional skip became a real test. Eighth 2026-09-06: `12327 passed, 17 skipped, 0 failed in 1987.61s` (rc=0). Ninth 2026-09-07: `12372 passed, 17 skipped, 0 failed in 1962.70s` (rc=0), `SUITE TREE STILL` — run while the tree was deliberately untouched for the whole 32 minutes, which is the only way that verdict means anything. Tenth 2026-09-07: `12378 passed, 17 skipped, 0 failed in 1988.05s` (rc=0), `SUITE TREE STILL`. Eleventh 2026-09-07: `12393 passed, 17 skipped, 0 failed in 1983.63s` (rc=0), `SUITE TREE STILL`. Twelfth 2026-09-07: **RED — `9 failed, 12405 passed, 19 skipped in 2308.06s` (rc=1), `SUITE TREE STILL`** — the streak ended, and the run EARNED its place: it was deliberately moved onto the tree the previous item SHIPPED rather than the one before it, because that item deleted a module. Four failures were a regression introduced hours earlier (a process-wide singleton whose cache key omitted its root — three passed ALONE and failed TOGETHER, the cross-test-pollution signature nothing else detects) and five were a unit test doing live DNS through an SSRF guard that binds its resolver at import. Both fixed in DEBT-179. **A green streak is not evidence that the next run is green; running it BEFORE the change would have found neither.** Thirteenth 2026-09-07: `12416 passed, 17 skipped, 0 failed in 1848.06s` (rc=0), `SUITE TREE STILL` Fourteenth 2026-09-07: `12423 passed, 17 skipped, 0 failed in 1953.18s` (rc=0), `SUITE TREE STILL` — launched deliberately because `src/` had drifted ONE item from the last green, which is the right reason to spend 32 minutes and the only one that makes the verdict worth having. — green again on the tree that carried both fixes, and the skip count fell 19 -> 17, corroborating the transient-DNS diagnosis: two tests that SKIPPED during the red run execute again. The false claim survived HERE after
  `CLAUDE.md` was corrected, and because this file is what the loop reads on every
  invocation, no invocation ever ran it — which is how TEN tests sat red, every one of
  them a retired thing whose tests stayed behind. "It hangs" reads as *impossible*, so
  nobody tries. Run `./scripts/full_suite.sh` (detached, stamped log) and collect it
  later; a foreground timeout kills it mid-run, which is all "hangs" ever was.

  **A verdict is only about ONE tree, and editing during the run voids it.** The script
  says so itself: `SUITE TREE STILL` means the fingerprint held, `SUITE TREE CHANGED
  DURING THE RUN` means it did not. Measured 2026-09-06: a run failed on
  `test_pid_cleanup_handler_no_longer_raises_systemexit`, which reads
  `inspect.getsource(orchestrator)` FROM DISK — the file had been edited mid-run, so the
  failure was an artefact and the verdict void. Launch it, then keep your hands off the
  tree until it prints, or you will spend thirty minutes buying a number that means
  nothing.

  Targeted paths stay right for the edit loop, but the full run is the ONLY detector for
  cross-test pollution and for a retirement that left its tests behind — and no tripwire
  can replace it: a static scan cannot tell a test asserting a dropped table EXISTS from
  one asserting it is GONE. Four attempts at that regex failed before this was measured.

  A hanging test is a failing test — but only after you have checked it is not merely
  slow. Pre-existing red is in scope: root-cause it, fix it, and say so.
- **validate** — restart with `./start.sh`, then verify via `~/.stackowl/logs/stackowl.jsonl`,
  never a PID. **A deletion is not live until the process holding the old code is gone** —
  check the core's start time against your last commit before believing any measurement.
- **document** — close the doc to `DOC_STANDARD`. **Run its Verification section**; do not
  trust it. Stamp `Last verified` with a date AND a commit.

### Honest validate

For each acceptance check, record the evidence you actually obtained. A check you could not
evidence stays **OPEN** with the query that would close it. Two failures this programme has
already paid for:

- An acceptance check whose only evidence line was at DEBUG while production runs at INFO.
  No volume of traffic could ever have closed it. **If a log line is the evidence for a
  claim, it must be INFO — and run the closing query before you need it.**
- A fix that worked in tests and never fired in production, because the path that would
  trigger it was not taken. "The turn succeeded" is not "my change works."

**A `partial` stage carries a `closing_check` — a one-liner printing OPEN or CLOSEABLE —
and `progress_lint` refuses one without it.** Run this beside `escalation_check` at the
start of a loop:

```bash
uv run python scripts/validate_check.py
uv run python scripts/doc_check.py      # which design docs went stale
```

**`doc_check.py` is the THIRD instance of one cure.** `DOC_STANDARD` requires
`Last verified: <date>, against commit <sha>` beside a `Source:`, which makes
staleness checkable — and nothing checked it, so the claim aged exactly as an
escalation's premise aged before `premise_check` and a `partial` stage's evidence
aged before `closing_check`. MEASURED 2026-09-07 over 84 documents: **25 stale, 45
measurable, 39 unmeasurable**. It reports STALE and UNMEASURABLE separately and
names both — a stale count that hid the unmeasurable would be the denominator
error this programme pays for most. **The first version read 15 stale / 51
unmeasurable, and eleven of that difference was MY PARSER, not the corpus**: it
missed `Source (new):`-style variants and src-relative paths, so documents that
had followed the convention were filed as having no header at all. The remaining
39 are a real divergence — 34 documents use an `**Item.** / **Ask.**` prose genre
DOC_STANDARD does not describe. It is NOT
a gate: 15 of 33 measurable docs are stale today, and a tripwire would fail every
unrelated change until someone re-read fifteen documents, which is how a gate gets
bypassed rather than satisfied.

**Why this is executable and not a note.** The rule above was written down and never
enforced. MEASURED 2026-09-06: ten stages were `partial`, all of them `validate`, and NOT
ONE carried a runnable field. Three had a closing query in English inside `changes:`, where
nothing could execute it; the other SEVEN had nothing at all — so they were dead ends, not
open questions, and no later pass could ever close them. Ten items sat at 6/7 with no
mechanism that could advance them. The escalation queue had already been given exactly this
cure (`premise_check`) for exactly this reason, and validates never got it. First run:
D07.2 and D07.3 had been closeable for days.

**Verify a CLOSEABLE before you believe it** — the first run also produced a false positive.
D15.6's check asked for a file containing both `notification_overrides` and `SELECT`, and
`store_cadence.py` names the table in a cadence declaration and says SELECT about something
else. A conjunction across a whole file is not a statement.

**A ZERO IS AMBIGUOUS — make sure the evidence CAN exist.** A check returning 0 means
either "not yet" or "never possible", and only one of those is an open question. MEASURED
2026-09-06: D14.4's check grepped the logs for `health sweep found unhealthy`, a string
`_compose_alert` RETURNS to the alert sink and which nothing logs — the sweep found
unhealthy subsystems 735 times while that phrase appeared in the logs zero times, ever. It
would have read OPEN forever. Written by me hours after this very section told me to run
the closing query first; I ran it, got 0, and read the 0 as provisional.
`tests/audit/test_a_closing_check_looks_for_evidence_that_can_exist.py` now asks the
LOGGERS — every log-based pattern must be a string some `log.*` call actually emits, at
INFO or above. A string merely PRESENT in the tree proves nothing about the logs.

**PREFER EVIDENCE ONLY THE NEW CODE COULD PRODUCE.** A check can be bounded, and its
line can exist, and it can STILL not close anything — because the code you replaced
would have satisfied it too. MEASURED 2026-09-06: DEBT-128's check grepped for "token
budget seeded from prior attempts" and reported CLOSEABLE on four seedings across two
tasks. But both attempts of each task shared ONE trace id (`recover-task-<suffix>`
reuses its suffix), and that is exactly the shape the OLD code already handled —
`get_turn_token_totals(trace_id)` would have found those rows as well. The check was
satisfied by a case that cannot tell the fix from its predecessor.

It closed anyway, on a DIFFERENT question: `tasks.accumulated_input_tokens` was created
by the fix's own migration and has exactly one writer, so a non-zero value is impossible
without the new code. **That is the shape to reach for** — a new column, a new log
FIELD, a new message — not a pre-existing line that merely fires again.

This is the third variant of one family, and naming all three together is the point:
satisfied by HISTORY (DEBT-125, fixed with date bounds), evidence that CANNOT EXIST
(DEBT-127, fixed by asking the loggers), and satisfied by a case the OLD CODE HANDLED
TOO. A date bound answers *when*, not *by what*.

No guard is shipped for this and the reason is recorded: the obvious proxy — "was the
pattern introduced before the fix?" — is measurably unreliable. Run against the live
checks it mislabelled D14.4 and DEBT-136 as weak, because their discriminating
fragments (`"remedies": ["`, `"recurring": true`) are JSON rendered at LOG time and
appear in no source file. A check that cries wolf on correct work is the failure this
programme keeps paying for, so this stays a rule you apply rather than one a script
enforces.

**If you cannot write the check, the premise is too vague — fix the premise.** D11.3's
evidence was a frame rendered as text to the model and logged NOWHERE, so no volume of
traffic could ever have closed it: the DEBUG-evidence failure above, one step worse. Being
forced to write the check is what found it, and an INFO line was added so the claim became
one reality could settle.

## Stop and brief the operator

Do not proceed autonomously past any of these. Write the brief into `current.ESCALATIONS`
and move to work that is not blocked:

1. Removing or disabling anything user-facing.
2. A destructive migration, or any data deletion.
3. Editing a code block shared with consent or clarify, while its smoke suite is red.
4. Three failed fix attempts on one problem — that is an architecture question, not a fourth attempt.
5. A panel answer that contradicts a recorded decision.

## Git

Commit at sub-story granularity when green. Merge to main and push when the item is green.
Never push `do_not_push_to_git_research_only/`.

## Mutation-testing restores from a FILE COPY, never from git

**Measured twice on 2026-09-03, in one session.** `git checkout <file>` after a
mutation reverted a real edit made earlier in the same item — git does not know
which of the file's changes were the experiment. On an UNTRACKED file the same
command fails SILENTLY, and with `|| true` after it, a mutated module sat in the
tree looking green.

    cp <file> $SCRATCH/f.bak   # before mutating
    <mutate, run the test, confirm it goes red>
    cp $SCRATCH/f.bak <file>   # restore
    <re-run: it must go green again>

The final re-run is not optional; it is the only proof the restore happened.

## Landmines

- **Never run `graphify update` or `graphify hook install`.** The hook actively suggests
  `graphify update` when the graph is stale — **ignore it.** It collapses a 12k-node graph to
  under 700. Refresh with `/graphify src` manually.
- `graphify query` is scoped to `src/` only. Useless for `progress.yml`, `docs/`, `tests/`.
- No vendor names in `src/`, `tests/` or `scripts/` — say "the reference platform".
- Every `except` logs. 4-point logging on every new `execute()`.

## Done means

All seven stages `done` or an explicit `no_change_needed`; the document closed with its
Verification section run; `progress_lint` clean; baselines held; pushed. Then advance
`current` to the next item and report what was decided by panel, what was escalated, and
what could not be evidenced.
