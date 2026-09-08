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

## EVERY LOOP FINISHES SOMETHING

**Bakir, 2026-09-07: each loop must achieve the goal for that loop and make progress.**

An invocation ends with something DONE and pushed — not a diagnosis handed forward, not
a report of what the next loop should do, not thirty minutes of watching a test run. The
record of the work is not the work.

**These are the shapes he is ruling out, and each has happened here:**

* **Diagnose and defer.** A loop measured a defect completely, wrote it up, and filed the
  fix for "the next loop". If the diagnosis is complete, the fix is the same loop's job.
* **Blocked as an outcome.** A loop ended with every stage `blocked` on a full-suite run
  it had launched itself. Launching a 32-minute verdict is a CHOICE; making it the
  loop's only content is choosing to do nothing.
* **Polling as work.** Reading a progress bar is not progress.

**So: never let a wait consume the loop.** The full suite fingerprints `src/` and
`tests/` only — `docs/`, `scripts/` and `progress.yml` are free the whole time, and
committing is what actually voids a verdict, not editing. If a run is in flight, do the
part of the item that does not touch the fingerprint and land it after; if there is no
such part, DO NOT LAUNCH THE RUN until the item is otherwise finished.

**And prefer the smallest complete thing over the largest partial one.** A drained
report, a closed check, one document corrected end to end — done, gated, pushed — beats
a deeper investigation that ends in a note. When an item genuinely cannot finish, say so
in one line with what would unblock it, and then FINISH SOMETHING ELSE in the same loop
rather than reporting the block as the result.

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
  18 skipped, 0 failed in 1911.85s` (2026-09-06). Sixth green 2026-09-06: `12284 passed, 18 skipped, 0 failed in 1979.40s` (rc=0), `SUITE TREE STILL`. Seventh 2026-09-06: `12312 passed, 17 skipped, 0 failed in 2146.47s` (rc=0) — the skip count FELL because an unconditional skip became a real test. Eighth 2026-09-06: `12327 passed, 17 skipped, 0 failed in 1987.61s` (rc=0). Ninth 2026-09-07: `12372 passed, 17 skipped, 0 failed in 1962.70s` (rc=0), `SUITE TREE STILL` — run while the tree was deliberately untouched for the whole 32 minutes, which is the only way that verdict means anything. Tenth 2026-09-07: `12378 passed, 17 skipped, 0 failed in 1988.05s` (rc=0), `SUITE TREE STILL`. Eleventh 2026-09-07: `12393 passed, 17 skipped, 0 failed in 1983.63s` (rc=0), `SUITE TREE STILL`. Twelfth 2026-09-07: **RED — `9 failed, 12405 passed, 19 skipped in 2308.06s` (rc=1), `SUITE TREE STILL`** — the streak ended, and the run EARNED its place: it was deliberately moved onto the tree the previous item SHIPPED rather than the one before it, because that item deleted a module. Four failures were a regression introduced hours earlier (a process-wide singleton whose cache key omitted its root — three passed ALONE and failed TOGETHER, the cross-test-pollution signature nothing else detects) and five were a unit test doing live DNS through an SSRF guard that binds its resolver at import. Both fixed in DEBT-179. **A green streak is not evidence that the next run is green; running it BEFORE the change would have found neither.** Thirteenth 2026-09-07: `12416 passed, 17 skipped, 0 failed in 1848.06s` (rc=0), `SUITE TREE STILL` — green again on the tree that carried both fixes, and the skip count fell 19 -> 17, corroborating the transient-DNS diagnosis: two tests that SKIPPED during the red run execute again. Fourteenth 2026-09-07: `12423 passed, 17 skipped, 0 failed in 1953.18s` (rc=0), `SUITE TREE STILL` — launched deliberately because `src/` had drifted ONE item from the last green, which is the right reason to spend 32 minutes and the only one that makes the verdict worth having. Fifteenth 2026-09-07: `12438 passed, 17 skipped, 0 failed in 1963.61s` (rc=0), `SUITE TREE STILL` — on the tree carrying the log-rotation fix, and the run was protected by HOLDING FOUR COMMITS for 33 minutes: the fingerprint covers every `src/` and `tests/` .py file, so a single edit would have printed SUITE TREE CHANGED and voided it. Waiting is cheaper than re-running. Sixteenth 2026-09-07: `12443 passed, 17 skipped, 0 failed in 1971.13s` (rc=0), `SUITE TREE STILL` — launched because `tests/` had drifted 119 lines from the last green, and the wait was again spent on doc-only work that needs neither src/ nor tests/. Seventeenth 2026-09-08: `12536 passed, 17 skipped, 0 failed in 2105.32s` (rc=0), `SUITE TREE STILL`, on `5e1d74a8` — earned by 42 files / 3,237 insertions / 21 commits since the last COMPLETED verdict. **AND THE FIRST ATTEMPT WAS VOIDED BY THE LOOP ITSELF.** The line above says docs/, scripts/ and progress.yml are free during a run. That is true of the FINGERPRINT and false of the SUITE: editing `scripts/doc_check.py` mid-run broke `tests/audit`, which imports it, and the run would have reported that failure while still printing `SUITE TREE STILL` — the fingerprint cannot see what it does not cover. MEASURED the same day: 15 test files read `progress.yml` and 29 reference `docs/`, so BOTH of those are in the blast radius as well. FREE DURING A RUN MEANS NOTHING UNDER TEST IMPORTS IT, and of the three named here, none of them fully qualifies. **EIGHTEENTH 2026-09-08: RED — `1 failed, 12553 passed, 17 skipped in 2239.34s` (rc=1), `SUITE TREE STILL`, on `c001b6c8`. The failure was MINE and the full run was the only thing that could see it: DEBT-228 changed `recover`'s contract and `tests/test_story_7_1b.py` asserts the retired one. That file sits DIRECTLY in `tests/`, so the targeted `tests/scheduler` run could not reach it and the gate runs only `@pytest.mark.tripwire`. The landmine below already said `tests/<package>` never runs `tests/*.py` — I had read it, and it did not help, because a warning names the HAZARD and the reader needs the ANSWER. `scripts/tests_touching.py` now gives it: run it BEFORE choosing a targeted path. See DEBT-231.** **NINETEENTH 2026-09-08: GREEN — `12568 passed, 17 skipped, 0 failed in 2188.04s` (rc=0), `SUITE TREE STILL`, on `39cd90d6`, carrying the fixes for the red run above. AND IT WAS LAUNCHED AT THE START OF A LOOP, NOT THE END — which is the sequencing rule this programme paid for three times: a run started when an item finishes guarantees the NEXT loop opens blocked. Start it first and work the item OFF-TREE in the scratchpad while it runs; one loop then absorbs the whole 37 minutes instead of blocking the next. DEBT-234 is the proof — measured, designed, implemented, mutation-proven and recorded entirely against a scratch copy, then applied in minutes when the verdict printed. The wait also bought the item's best catch: asking WHICH TESTS DEPEND ON THE BEHAVIOUR I AM CHANGING found a recorded decision the change contradicted, before it could turn `tests/audit` red.** **TWENTIETH 2026-09-08: GREEN — `12577 passed, 17 skipped, 0 failed in 2212.17s` (rc=0), `SUITE TREE STILL`, on `a937743d`. The sequencing rule used DELIBERATELY for the first time: launched at the START of the loop, and DEBT-236 — a 35-id record loss, its 22-entry recovery from git history, three guards and a document correction — was measured, built, merged and dry-run ENTIRELY against a scratch copy of `progress.yml` while it ran, then applied in minutes. One loop absorbed the 37 minutes and lost nothing.** Doc-only work during a wait is still right — it just has to be HELD, not committed and not left on disk mid-run. The false claim survived HERE after
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

**AND A `done` VALIDATE MUST NAME ITS EVIDENCE — a `validated…` key, or a `doc:` whose
Verification section holds it.** MEASURED 2026-09-08, on my own record from the loop
before: DEBT-230's entry was DRAFTED IN THE SCRATCHPAD while a full suite ran and applied
verbatim when the tree came free, so it asserted a validate that never happened — the
platform was not restarted and no live record was read. `done` is the word that stops
anybody looking again, and it was the one stage with no check at all. `progress_lint`
now reports the population (43 legacy records) and
`tests/audit/test_a_done_validate_names_its_evidence.py` ratchets it so it can only fall.
**Do not draft a record ahead of the work it describes** — write the stage when the
evidence exists, and `partial` + a `closing_check` when it does not yet.

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

**`log_since.sh`'s THIRD ARGUMENT EXCLUDES — it is not a second pattern to match.**
MEASURED 2026-09-08: a check reading
`log_since.sh 2026-09-08 'runner.verify: exit' 'unverifiable'` reported
**CLOSEABLE — 60 boots** before the fix had run once, because it had counted every
summary that did NOT contain the word. The signature is
`<date> <grep-pattern> [exclude-pattern]`, and I assumed a conjunction from a signature I
never read. Put the AND in `jq`; give `log_since.sh` the date bound and ONE pattern.

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

**WATCH THE BRANCH THAT EMITS THE LINE, NOT THE EVENT THE PROSE NAMES.** A check can be
bounded, its line can exist, only the new code can produce it — and it can still name the
WRONG EVENT, one step upstream of the emitter. MEASURED 2026-09-07: D04.5's open check
said it *"needs a tool breaker to open, and there have been zero such events in five
days."* Both halves were false. **Three breakers opened after the fix shipped, one on
the shipping day itself** — so a reader watching the named event would have seen it fire
three times, seen no decline line, and concluded the fix was broken. And the open is not
the trigger: `request_escalation` sits behind `progress.is_open(name)` in `_dispatch`,
reached only when the model dispatches that tool AGAIN. **52 opens produced 12 bounces**
— 77% of the named event never reaches the emitter, by design.

The premise was written from the document's own narrative rather than from the branch
guard above the `log.*` call. So: **find the emitter, read what guards it, and name THAT
in the check.** The same walk that `test_a_closing_check_looks_for_evidence_that_can_exist`
does to prove the string is emitted also lands you on the conditions that reach it — it
asks whether the line CAN be logged; you must also ask WHEN. No guard for this either,
for the reason recorded above: an emitter's guard chain is not mechanically translatable
into a premise, and a script that guessed would cry wolf on correct work.

**And the third defect in that same check was mechanical**: it grepped
`~/.stackowl/logs/stackowl.jsonl`, one file, while the body of the SAME DOCUMENT cited
twelve events measured *"across every kept log"*. The document contradicted itself across
five sections and nothing noticed, because a midnight-blind query returns 0 and 0 reads
as *not yet*. `doc_check.py` reports these as `BLIND AFTER MIDNIGHT` (43 commands in 22
documents as of 2026-09-07, down from 47/23); the executable checks in `progress.yml` are
already clean — **9 log-based checks, all 9 globbed, verified by a control that found
them.**

**A LOG-READING CHECK BECOMES A RECORD WHEN ITS EVIDENCE ROTATES.** The seven variants
above are all about what a check ASKS. This one is about how long the answer survives:
**the evidence had a shorter lifetime than the document.** A command that reads the logs
and records "PASS 2026-08-21: 8 occurrences" is a CHECK while the logs reach back that
far and a RECORD afterwards — and afterwards it returns 0 forever, which the next reader
takes as failure.

MEASURED 2026-09-07: four such commands in three documents, the oldest citing 2026-07-27
against logs that begin 2026-08-28. D16.3's was the sharpest — its evidence was a
throwaway plugin installed to prove the path and then REMOVED, so the line it greps
cannot fire again even in principle. `doc_check.py` reports these as `EVIDENCE OLDER THAN
THE LOGS`.

**Read the horizon from the FILES, never from the retention setting.** `backupCount` is
30 and `getFilesToDelete()` returns nothing, yet only ten dated files exist — the horizon
is YOUNG, not over-pruned, because a deletion incident on 2026-08-30 left two and daily
rotation has added one since. A detector keyed on the intended 30 would have reported
nothing while three documents cited evidence already gone.

So when a log-based check passes, write the date beside it AND keep something runnable
next to it — a test, or a command that proves the path still exists. When the evidence
ages out, say so and keep the measurement as the dated record it now is.

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

## A scripted edit REPLACES A SPAN — it never inserts at an offset

**MEASURED 2026-09-07, and it is the second time in one session.** The rule already
here — read the file back after a scripted edit — was followed, and it still went
wrong, because I read back the WRONG PROPERTY.

The edit appended a sentence to the full-suite history by finding an anchor and
inserting at the next newline:

    i = s.index(anchor) + len(anchor)
    j = s.index("\n", i)          # <- assumes the sentence ends at the line break
    s = s[:j] + add + s[j:]

**In a hard-wrapped paragraph a newline is not a sentence boundary.** The insertion
landed inside "The false claim survived HERE after `CLAUDE.md` was corrected",
splitting it, and THIS FILE — the one the loop reads on every invocation — shipped
reading "...survived HERE after Fifteenth 2026-09-07: `12438 passed...".

The read-back was `grep -c "12438"`, which returned 1. **That proves the text landed.
It proves nothing about WHERE.** A count is not a position.

So: **name both ends.** `assert old in s` then `s.replace(old, new, 1)` cannot split a
sentence, because the span you are replacing is explicit and the assertion fails loudly
when the file does not hold what you think it holds. An offset computed from an anchor
is a guess about where a sentence ends, and prose wraps.

And when you read back, read back the SPAN — print the sentence that now surrounds your
change, not a count of the token you inserted.

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
- **Never pick a targeted test path by hand — derive it.** `uv run python
  scripts/tests_touching.py` (no args = your working-tree changes) names every test that
  imports OR NAMES each changed module, marks with `*` the ones sitting directly in
  `tests/` that no `tests/<package>` path can reach, and prints the ready-made `pytest`
  line. The string-mention half is not optional in this tree: tests monkeypatch by dotted
  name and read `inspect.getsource` of modules they never import, so an import-only walk
  returns a confident, incomplete list.
- No vendor names in `src/`, `tests/` or `scripts/` — say "the reference platform".
- Every `except` logs. 4-point logging on every new `execute()`.

**`grep <glob> | tail` DOES NOT GIVE YOU THE NEWEST RECORD.** MEASURED 2026-09-07,
on a number this loop had just measured and was one edit away from writing into a
design document as fact. In this harness `grep` is a shell FUNCTION wrapping a
MULTI-THREADED grep, which emits results as workers finish rather than in argument
order: three identical runs of
`grep -h <pattern> ~/.stackowl/logs/stackowl*.jsonl | tail -1` returned last-line
timestamps **2026-09-03, 2026-09-04 and 2026-09-03** while the true newest record was
**2026-09-07** — the current log file landed SEVENTH of eleven in the output stream.
`/usr/bin/grep` in a plain shell IS ordered, so the documents are right for a human
and wrong for this loop, which is the worst possible split.

**And this exposure was CREATED by the cure for the previous one.** The
`BLIND AFTER MIDNIGHT` rule exists to move these queries off the single
`stackowl.jsonl` onto the `stackowl*.jsonl` glob. The single file is at least
ORDERED; multi-file is exactly the case that is not. A **count** is unaffected —
which is why it hid, because every count in this corpus is right and nothing looked
wrong. Only the queries that pick ONE record are damaged, and those are the ones a
document uses to say "and here is the latest".

**The cure is one word: `| sort |` before the `tail`.** Every line these logs contain
begins `{"ts": "` (0 non-conforming lines across the two largest retained files), so
a lexical sort IS a chronological sort, and it is correct under an ordered grep too.
`cat f1 f2 | jq` and `jq -c … f1 f2` consume their arguments in order and need
nothing. `doc_check.py` now reports `NEWEST RECORD TAKEN BY PIPE POSITION`, and
`tests/audit/test_the_newest_log_record_is_not_taken_by_pipe_position.py` pins both
the corpus and the `ts`-first property the cure depends on.

**`/usr/bin/grep` has the OTHER half of this, and needs `-a`.** The same control run
printed `binary file matches` on `stackowl-2026-09-05.jsonl` and silently returned
**98** of that file's **110** matches — the null-byte truncation `log_since.sh`
already bakes `-a` in for. Neither grep is safe bare: one misorders, the other
truncates. Write `grep -ah … | sort` and both are covered.

## Done means

All seven stages `done` or an explicit `no_change_needed`; the document closed with its
Verification section run; `progress_lint` clean; baselines held; pushed. Then advance
`current` to the next item and report what was decided by panel, what was escalated, and
what could not be evidenced.
