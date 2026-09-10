# CLAUDE.md

Rebuilt from what work in this repo has actually needed. Add a line only when it has
been earned — something non-obvious that cost time or nearly caused damage. Keep it
short; the previous version grew to 180 lines and was cleared on 2026-08-10.

## THE RULE ABOVE ALL OTHER RULES

**Bakir, 2026-08-31: always fix WHY it happened, never WHAT happened.**

This one is MANDATORY and it OUTRANKS everything else in this file, in
`PROCESS.md`, in every skill and in every loop. Where another rule would let you
ship a repair for the reported symptom, this rule overrides it.

A report from the operator is EVIDENCE OF A CAUSE, not a work item. "The reply
had asterisks" is what happened; "nothing ever told the model where its answer
was being delivered, so output shape could only be stored as one person's
preference" is why. Fix the second and the first cannot recur. Fix the first and
you have bought one quiet day.

**Never defer the root cause to an escalation while shipping the symptom fix.**
That is the specific move he rejected: the formatter was repaired, the reason it
mattered was filed as a question for him, and he answered "you did fix only my
ask but you should fix the core off issue why it is happening not what
happaned". Escalate a DECISION he alone can make. Never escalate the DIAGNOSIS
to avoid doing it.

Before any fix is called done, answer in writing: *what made this possible, and
what else does that same cause reach?* If the answer is only a restatement of
the symptom, the root cause has not been found yet.

## EVERY LOOP FINISHES SOMETHING

**Bakir, 2026-09-07: each loop must achieve the goal for that loop and make progress.**

An invocation ends with something DONE and pushed — not a diagnosis handed forward, not a
note about what the next pass should do, not thirty minutes spent watching a test run.
The record of the work is not the work.

Ruled out, each having happened here: **diagnose and defer** (a complete diagnosis filed
as the next loop's job — if the diagnosis is done, the fix is the same loop's job);
**blocked as an outcome** (every stage `blocked` on a full-suite run this session
launched itself); **polling as work**.

The full suite fingerprints `src/` and `tests/` only, and it is COMMITTING that voids a
verdict, not editing — `docs/`, `scripts/` and `progress.yml` stay free the whole time.
So never let a wait consume the loop: work the unfingerprinted part and land it after, or
do not launch the run until the item is otherwise finished. Prefer the smallest complete
thing over the largest partial one.

## RETIRED MEANS DELETED

**Bakir, 2026-09-01: "Whatever retired should be deleted from code and we should
never have dead code."**

Retiring something means deleting its code, its registration, its tests, its
scheduler/job rows **and the design document's claim about it** — in the SAME
change as the retirement. Not
registered-but-unscheduled. Not empty-but-present. Not "kept as a seat for a
future feature". Git history holds the old code; the tree holds what runs.

This rule is earned. Every dead thing left in this tree has cost diagnosis time
because it *looked* live: `committed_facts` retired to zero rows by migration
0112 while every recall path still queried it; `DreamWorker` kept as a
deliberately empty seat; `job_queue` with **zero references anywhere in `src/`**;
`committed_facts_fts` still indexing 1,112 rows of content whose writer was
removed.

**And the mirror, which this list used to get wrong.** `objectives` was named here
as dead on the strength of a zero-row table and a driver ticking every 60s against
it. It is NOT dead: the `objective` tool has ten recorded invocations and
`objective_subgoals` holds 28 rows, newest 2026-08-27 — an empty PARENT over a
populated CHILD. Re-measured 2026-09-03, after this file's own wording sent a
session looking for 2,517 lines to delete. **The zero-row table is the question,
never the answer** — and a document asserting deadness is not a measurement.

Two things to check before deleting, and only these two: that it is genuinely
unreferenced (**measure it**), and that removing it does not remove something
that was *bounding* or *triggering* another component. Then delete the WRITER,
not just the rows — a row deleted while its writer lives is re-seeded on the next
boot (migration 0125 vs scheduler assembly, thirty-one seconds apart, every boot).

**THE DOCUMENT IS THE FIFTH SURFACE, and it was missing from this list until
2026-09-07.** `d8b8ba81` deleted the tool-loop hard stops properly — code, config
fields, tests and the module docstring, one commit. D05.7 went on saying
`hard_stop_enabled` defaults **False**, advertising an operator-tunable flag, for
eight days. Not a careless retirement: a checklist that did not name the surface.
MEASURED the same day — of 19 stale design documents, **ELEVEN were made stale by a
commit that SUBTRACTED something** (`quirks`, LanceDB, `retry_queue`, the fact-store
machinery, a 600s cap, a notification cap). `doc_check.py` now separates them:
staleness by date says a source MOVED, and cannot say it was REMOVED — behind versus
actively false are different kinds of wrong.

**Find dead code while doing something else? Delete it then.** Do not file it as
debt.

## Read these first

`progress.yml` is the state of record — `current` says where we are. Then
`docs/reference-mapping/PROCESS.md` (the method) and `DOC_STANDARD.md` (what every
document must contain). They carry the working rules; this file only holds what they
do not.

## Landmines

**Never run `graphify update` or `graphify hook install`.** Both were tried and
rejected: the packaged incremental path collapses a 12k-node graph to under 700 on a
single-file change, and `update <path>` writes to `<path>/graphify-out/` instead of the
repo-root graph this project uses. The PreToolUse hook actively suggests `graphify
update` when the graph is stale — **ignore that suggestion.** To refresh, re-run
`/graphify src` manually. Confirmed still live 2026-08-11: the hook fired repeatedly
across a full session.

**`graphify query` is oriented at `src/` only.** It cannot answer questions about
`progress.yml`, `docs/`, or `tests/`, and on a broad question it returns ~1,100 nodes,
which is worse than useless. Use it for "where does X live in src", not for everything.

**A full `pytest` run does NOT hang — it takes ~30 minutes, and THE SUITE IS GREEN.**
`./scripts/full_suite.sh` runs it detached and the log ends with its own verdict, so
collecting it is `grep -c 'SUITE DONE'` (0 = running, 1 = finished) rather than a guess
at a process table. Measured, in order:

| when | result |
|---|---|
| 2026-09-01 | `6 failed, 11440 passed, 19 skipped in 1885.47s` |
| 2026-09-03 | `10 failed, 11853 passed, 18 skipped in 1775.99s` |
| 2026-09-04 | **`11857 passed, 18 skipped, 0 failed in 1731.31s` (rc=0)** |
| 2026-09-05 | **`12123 passed, 18 skipped, 0 failed in 1880.78s` (rc=0)** |
| 2026-09-06 | **`12170 passed, 18 skipped, 0 failed in 1911.85s` (rc=0)** |
| 2026-09-06 | **`12197 passed, 18 skipped, 0 failed in 1928.85s` (rc=0)** — fourth green |
| 2026-09-06 | **`12215 passed, 18 skipped, 0 failed in 1941.48s` (rc=0)** — fifth green |
| 2026-09-06 | **`12284 passed, 18 skipped, 0 failed in 1979.40s` (rc=0)** — sixth green, `SUITE TREE STILL` |
| 2026-09-06 | **`12312 passed, 17 skipped, 0 failed in 2146.47s` (rc=0)** — seventh green; skips 18→17, an unconditional skip became a real test |
| 2026-09-06 | **`12327 passed, 17 skipped, 0 failed in 1987.61s` (rc=0)** — eighth green, `SUITE TREE STILL` |
| 2026-09-07 | **`12372 passed, 17 skipped, 0 failed in 1962.70s` (rc=0)** — ninth green, `SUITE TREE STILL` |
| 2026-09-07 | **`12378 passed, 17 skipped, 0 failed in 1988.05s` (rc=0)** — tenth green, `SUITE TREE STILL` |
| 2026-09-07 | **`12393 passed, 17 skipped, 0 failed in 1983.63s` (rc=0)** — eleventh green, `SUITE TREE STILL` |
| 2026-09-07 | **`9 failed, 12405 passed, 19 skipped in 2308.06s` (rc=1)** — RED, streak ended; see DEBT-179 |
| 2026-09-07 | **`12416 passed, 17 skipped, 0 failed in 1848.06s` (rc=0)** — green again on the fixed tree; skips 19→17 as the DNS-flaky tests ran |
| 2026-09-07 | **`12423 passed, 17 skipped, 0 failed in 1953.18s` (rc=0)** — fourteenth; run deliberately because src had drifted one item from the last green |
| 2026-09-07 | **`12438 passed, 17 skipped, 0 failed in 1963.61s` (rc=0)** — fifteenth green, `SUITE TREE STILL`; the tree carrying the log-rotation fix (DEBT-196) |
| 2026-09-07 | **`12443 passed, 17 skipped, 0 failed in 1971.13s` (rc=0)** — sixteenth green, `SUITE TREE STILL`; launched because `tests/` had drifted 119 lines from the last green |
| 2026-09-08 | **`12536 passed, 17 skipped, 0 failed in 2105.32s` (rc=0)** — seventeenth green, `SUITE TREE STILL`, on `5e1d74a8`. Earned: 42 files / 3,237 insertions / 21 commits had landed since the last COMPLETED verdict. THE FIRST ATTEMPT WAS VOIDED BY ME — I edited `scripts/doc_check.py` mid-run believing `scripts/` was outside the fingerprint. It is; it is NOT outside the SUITE, because `tests/audit` imports it. **The fingerprint is not the blast radius.** 15 test files also read `progress.yml` and 29 reference `docs/`, so those are in it too. Free during a run means NOTHING UNDER TEST IMPORTS IT. |
| 2026-09-08 | **`1 failed, 12553 passed, 17 skipped in 2239.34s` (rc=1)** — RED, on `c001b6c8`, `SUITE TREE STILL`. The failure was MINE: DEBT-228 changed `recover`'s contract and `tests/test_story_7_1b.py` asserts the retired one. It sits DIRECTLY in `tests/`, so the targeted `tests/scheduler` run could never reach it and the gate runs only tripwires. **The landmine below was already written and did not help** — it names the hazard, and the reader needs the answer. `scripts/tests_touching.py` now gives it. See DEBT-231. |
| 2026-09-08 | **`12568 passed, 17 skipped, 0 failed in 2188.04s` (rc=0)** — nineteenth, GREEN again on `39cd90d6`, the tree carrying the fixes for the red run above. **Launched at the START of a loop rather than the end**, which is the sequencing rule three loops paid for: a run begun when an item finishes guarantees the NEXT loop opens blocked. Start it first, work the item off-tree in the scratchpad, land it when the verdict prints — one loop absorbs the 37 minutes instead of blocking the next. See DEBT-234. |
| 2026-09-08 | **`12577 passed, 17 skipped, 0 failed in 2212.17s` (rc=0)** — twentieth, on `a937743d`, `SUITE TREE STILL`. First DELIBERATE use of the sequencing rule: launched at the start of the loop and the whole of DEBT-236 built against a scratch copy of the record while it ran. |
| 2026-09-08 | **`12583 passed, 17 skipped, 0 failed in 2188.25s` (rc=0)**, `SUITE TREE STILL` — twenty-first, on `f22bfd32`. Launched at the START of the loop again; DEBT-239 — a new `doc_check` report, a `tests_touching` blind spot, ten guards and four document corrections — was measured, built, mutation-proven and dry-run entirely off-tree while it ran. |
| 2026-09-09 | **`12682 passed, 17 skipped, 0 failed in 2272.25s` (rc=0)**, `SUITE TREE STILL` — twenty-second, on `15ec3c83`. Launched at the START of the loop; DEBT-257 was measured, built, mutation-proven and dry-run against an off-tree MIRROR (`docs/`, `src/` and `.git` symlinked) for the whole 38 minutes. **And the rule that a commit voids a verdict was VERIFIED rather than trusted**: `tree_fingerprint` hashes `git rev-parse HEAD` alongside the `src/`+`tests/` listing, so a docs-only commit voids it too — and the script then re-runs itself once, so the cost is another 38 minutes, not a lost answer. |
| 2026-09-09 | **`12711 passed, 17 skipped, 0 failed in 2420.56s` (rc=0)**, `SUITE TREE STILL` — twenty-third, on `d27ae41a`. Launched at the START of the loop; DEBT-261 was measured, authored and dry-run against scratch copies for the whole 40 minutes and applied when the verdict printed. The measurement was the loop's substance: it REFUTED two recorded product-defect claims by grouping on `.msg` before counting — `stream-miss` is THREE messages, not one, and only 186 of 236 are a loss. |
| 2026-09-09 | **`12721 passed, 17 skipped, 0 failed in 2372.38s` (rc=0)**, `SUITE TREE STILL` — twenty-fourth, on `b6a991f2`. Launched at the START of the loop, per the note the previous loop left itself. DEBT-263 was measured, authored and dry-run off-tree for the whole 40 minutes, and the wait paid for itself twice: it caught that `Job` is a PYDANTIC model whose `idempotency_key`/`next_run_at` are required `str` (the drafted test passed None and would have failed on construction), and it bought a threshold calibrated against 319 real inter-sweep gaps rather than chosen. |
| 2026-09-09 | **RED — `1 failed, 12728 passed, 17 skipped in 2398.07s` (rc=1)**, `SUITE TREE STILL`, on `13b6894d` — twenty-fifth, and the failure was MINE: the previous loop's `Reviewed:` line on D14.4 says "the alert half this document records as honestly OPEN", a new `\bOPEN\b` line nobody had judged, and DEBT-256's ground-truth table demands a judgement for every occurrence. **THE GATE COULD NOT HAVE CAUGHT IT, BY DESIGN**: that test is deliberately not a tripwire, because it fires whenever a design document gains or edits an OPEN line — ordinary work here — and a gate that fails on ordinary work gets bypassed rather than satisfied. The cost of that trade is exactly this: a doc edit ships and the full run finds it 40 minutes later. Paid once, recorded, not re-litigated. |
| 2026-09-09 | **`12732 passed, 17 skipped, 0 failed in 2390.37s` (rc=0)**, `SUITE TREE STILL` — twenty-sixth, on `4b940381`, GREEN again on the tree carrying the fix for the red run above. Launched at the START of the loop because the previous verdict was rc=1, which is the right reason to spend 40 minutes. DEBT-265 — a `progress.yml`-only change — was authored and dry-run against a scratch copy for the whole run and applied when it printed: `progress.yml` is outside the FINGERPRINT and inside the SUITE, and a mid-run write is exactly what voided the 17th. |
| 2026-09-10 | **`12750 passed, 17 skipped, 0 failed in 2585.56s` (rc=0)**, `SUITE TREE STILL` — twenty-seventh recorded here and the 34th run, on `27299c47`. Launched at the START of the loop; DEBT-276 was measured, built, mutation-proven and dry-run against an off-tree MIRROR for the whole 43 minutes and applied when the verdict printed. **The wait paid for itself three times**: the new guard's own control test caught that `git log -S` cannot see a value change (`-G` can), deriving the targeted path with `tests_touching.py` found a THIRD stale copy of the same rationale sitting in `tests/`, and the guard caught me re-arming the trap inside the record of disarming it. |
| 2026-09-10 | **`12755 passed, 17 skipped, 0 failed in 2832.37s` (rc=0)**, `SUITE TREE STILL` — 35th run, on `fa5f9add`. Launched at the START of the loop; DEBT-277 was built off-tree for the whole 47 minutes, and Bakir's live report arrived mid-run and became DEBT-278 in the same window. **Both defects are the same shape and neither was found by a test**: an error message asserting a cause nobody checked — `web_fetch` said the browser runtime was down while it was serving other calls in the same second, and consent told him an approval had expired seconds after it resolved and ran. Diagnosis cost three abandoned readings, each refuted by evidence the platform already held. **When a message names a subsystem, check the subsystem before believing the message.** |
| 2026-09-10 | **`12765 passed, 17 skipped, 0 failed in 3076.03s` (rc=0)**, `SUITE TREE STILL` — 36th run, on `c4181862`. Launched at the START of the loop; DEBT-279 was measured, built, mutation-proven and dry-run against an off-tree mirror for the whole 51 minutes. **RANKING THE ALARM CHANNEL BY VOLUME IS HOW THE ITEM WAS FOUND, and three candidates ahead of it were already-settled**: the RCA consent refusals (DORMANT since 09-02), the apology cascade (last fired 09-07 — caught ONLY because a by-day breakdown contradicted a 3-day window that had included its final day), and `trending-research-owl` (ESC-127, correctly queued). **Always break a window down by day before believing it is current.** The mirror also produced FOUR audit failures that were pure artefacts — it lacked `.claude/` and `start.sh` — proven by running the same tests on the real tree both before and after applying. |
| 2026-09-10 | **`12775 passed, 17 skipped, 0 failed in 2473.42s` (rc=0)**, `SUITE TREE STILL` — 37th run, on `161d5550`. Two items landed on it: DEBT-281 (built the previous loop) and DEBT-282, which took the stale-document count to **0 for the first time** — and all ten had been made stale by THIS SESSION'S OWN commits. **`doc_check.py` caught me twice while I was fixing it, and both would have shipped silently**: my first pass REPLACED five existing `Reviewed:` lines, destroying earlier loops' judgements (the parser keeps only the last), and my second put the new shas AFTER the em-dash — `_reviewed_shas` reads `field.split("—", 1)[0]`, so they were prose. Nine stamps moved only four documents, and the COUNT is what exposed it. **Run the instrument after the edit, not before.** |
| 2026-09-10 | **`12784 passed, 17 skipped, 0 failed in 2603.72s` (rc=0)**, `SUITE TREE STILL` — 38th run, on `0923f8ad`. **THE ITEM THIS LOOP OPENED WITH WAS REFUTED BY ITS OWN MEASUREMENT, and that became DEBT-283.** I ranked the alarm channel by raw count, picked the loudest message (827) and built a dedupe on the reasoning that it must be re-announcing a static fact. Bracketing by process lifetimes killed it: **827 windows with exactly one occurrence, 269 with none, ZERO with more than one** — the lint already did what its docstring said. The fix was reverted rather than shipped as decoration. What 827 measured is **this box restarting: 580 of the 1,096 boots are CodeWatcher re-execs**. `retired_log_messages.py` now carries the denominator and marks a message that never fired twice in one process as a STARTUP FACT — and note the RATIO would not have caught it (0.75/boot, outside any sensible band); the MAXIMUM does. |
| 2026-09-10 | **`12796 passed, 17 skipped, 0 failed in 2838.93s` (rc=0)**, `SUITE TREE STILL` — 39th run, on `72be0655`. **THE GATE REFUSED THE COMMIT AND WAS RIGHT**, the third time today: DEBT-285's closing_check read `stackowl*.jsonl` through a raw `grep` with an `awk` date bound, and `test_no_closing_check_greps_the_logs_unbounded` requires every log-reading check to route through `log_since.sh` — a STRUCTURAL rule, because the script takes the date as a required argument and that is what makes the window impossible to forget. My awk bound was equivalent and it does not matter: a rule that has to be re-derived per check is the one that gets skipped. **Comply with the structural form rather than arguing the instance is safe.** |
| 2026-09-10 | **`12805 passed, 17 skipped, 0 failed in 2500.19s` (rc=0)**, `SUITE TREE STILL` — 40th run, on `f2cc3af0`. DEBT-287 built off-tree against a MIRROR for the whole 42 minutes. **AND THE MIRROR FAILED FOR A NEW REASON, which is worth more than the item:** `tests/audit` claimed no logging call emits the string my closing check greps — while the call sat in the mirror's own `src/`. `scripts/` was a SYMLINK, and `retired_log_messages.py` computes its root from `__file__.resolve()`, which follows the link back to the REAL repo, so the guard read a tree I had not edited. Replacing the symlink with a copy: 24 passed. **A mirror models the files it copies, never the ones a resolved path escapes to** — and `.resolve()` in a sibling script is invisible from the test that calls it. |
| 2026-09-10 | **`12866 passed, 17 skipped, 0 failed in 2557.18s` (rc=0)**, `SUITE TREE STILL` — 41st run, on `1be436ff`. DEBT-288 built off-tree for the whole 43 minutes. **AND THE MIRROR ALMOST WROTE THE REAL TREE, a THIRD distinct way it fails to model one:** staging the record "into the mirror" would have written the REAL `progress.yml` mid-run, because the mirror's copy was a SYMLINK to it — and 15 test files read that file, which is exactly what voided the 17th run. `write_text` on a symlink follows it silently; nothing announces it. It was caught only because `cp src dst` refused with "are the same file" and the `&&` short-circuited the write. **Assert `not path.is_symlink()` before any write to a mirror**, and prefer a real copy for anything you intend to edit. |
| 2026-09-09 | **`12738 passed, 17 skipped, 0 failed in 2414.98s` (rc=0)**, `SUITE TREE STILL` — twenty-seventh, on `e1dd243d`. Launched at the START of the loop; DEBT-267 was measured, built, mutation-proven and dry-run against an off-tree MIRROR for the whole 40 minutes. The wait paid for itself TWICE: it caught that my throwaway sweep's looser adjacency rule had inflated 237 asymmetric functions to **280**, one edit before that number went into this file as fact; and it caught that the new test read `caplog`, which reaches records only while `stackowl` still propagates — `configure_logging` sets `propagate = False`, so the guard would have passed alone and failed in any session that had configured logging first. It reads the named logger directly now. |
| 2026-09-09 | **`12742 passed, 17 skipped, 0 failed in 2403.74s` (rc=0)**, `SUITE TREE STILL` — twenty-eighth, on `2c538145`. DEBT-268 was measured, built, discrimination-proven and dry-run against an off-tree mirror for the whole 40 minutes. **AND THE PROGRESS BAR LIED AGAIN, exactly as this file warns.** At ~65 minutes elapsed it read 38%, and `ps` reported the pytest process with an `etime` of 22 minutes — together those look like a run that restarted itself. Both were red herrings: the CPU-time delta was +32s in 60s and the bar moved **38% -> 52% in that same minute**, because the early `tests/db` directories are a quarter of the clock. Check the CPU delta and the log's growth; never the percentage, and never `etime` on a pid matched by a loose pattern — the first `pgrep` hit was a transient process with 0 CPU. |
| 2026-09-09 | **`12747 passed, 17 skipped, 0 failed in 2409.67s` (rc=0)**, `SUITE TREE STILL` — twenty-ninth, on `e1102a1d`. DEBT-269 was measured, built and discrimination-proven for the whole 40 minutes WITHOUT WRITING TO `docs/` — the corrected `Reviewed:` text was fed to `doc_check._reviewed_shas` as a STRING, which proved the extraction goes empty -> `{sha}` while the tree stayed still. docs/ is outside the fingerprint and inside the SUITE, so that distinction is the whole technique. |
| 2026-09-09 | **`12748 passed, 17 skipped, 0 failed in 2404.49s` (rc=0)**, `SUITE TREE STILL` — thirtieth, on `d9ce9e31`. DEBT-271 was built off-tree for the whole 40 minutes, and the REASON is a consequence the previous loop created: DEBT-270 moved an AST walk into `scripts/retired_log_messages.py` and had `tests/audit` import it, which put that script inside the SUITE's blast radius. A one-source refactor can move a file from free-during-a-run to held-during-a-run, and nothing announces that. |
| 2026-09-09 | **`12752 passed, 17 skipped, 0 failed in 2403.14s` (rc=0)**, `SUITE TREE STILL` — thirty-first, on `bb6627a8`, and it is the verdict DEBT-272's `src/` change earned. DEBT-273 was measured and built off-tree for the whole 40 minutes, and the measuring is the story: a 232-event CURRENT signal turned out to be the platform WORKING, after two confident readings had to be abandoned. Both were built on a marker list without checking WHICH classifier ran — `db/pool.py` passes `is_dead=`, so the function I was reading is never called for the traffic I was explaining. **A shared helper with a caller-supplied override means the code you are reading may not be the code that ran.** |
| 2026-09-09 | **`12752 passed, 17 skipped, 0 failed in 2398.02s` (rc=0)**, `SUITE TREE STILL` — thirty-second, on `8b564c9e`, the verdict DEBT-273 earned. DEBT-274 built off-tree for the whole 40 minutes. **THREE LOOPS RUNNING, A CURRENT WARNING RESOLVED TO CORRECT BEHAVIOUR** — the dead-handle recycles, the schedule fulfiller's zero, the overclaim ladder. The alarm channel is now mostly explained, so the next real defect will be found by what is NEW in `retired_log_messages.py`'s CURRENT bucket rather than by re-reading what is already there. |
| 2026-09-09 | **`12753 passed, 17 skipped, 0 failed in 2412.01s` (rc=0)**, `SUITE TREE STILL` — thirty-third, on `f00c2ead`, the verdict DEBT-274 earned. DEBT-275 dropped two tables the code itself called "leftovers to delete", built off-tree against a MIRROR — and the mirror found its own limit: the unmodified `test_migration_count_is_15` GLOBS the repo's migration directory while the runner executes the mirror's, so it failed 140-vs-141 and passed the moment the file landed. **A mirror cannot model a test that compares the tree to itself.** |

The old line here said "it hangs on this box" and had said so since 2026-08-10. It was
wrong, and the wrongness was expensive twice over. "It hangs" reads as *impossible*, so
every run this programme makes is a targeted path — that habit let ELEVEN tests sit red
and a multi-tenancy tripwire sit dark while four unscoped statements landed. And when
THIS file was corrected on 09-01 the same claim survived in
`.claude/skills/item-loop/SKILL.md`, which is what the loop actually reads on every
invocation — so nothing changed and TEN more tests sat red. **Correcting one copy of a
rule is not correcting the rule.** All five surfaces now carry these numbers.

**AND THE CENSUS OF FIVE WAS ITSELF WRONG — SWEPT 2026-09-07, three days later.** Two
MORE live surfaces still said the suite hangs: `docs/reference-mapping/designs/D04.1.md`
("Never run a bare `pytest` on this box — it hangs") and, worse, **`SESSION_PROMPT.md`**,
which is the prompt handed to an autonomous session and which, as of 2026-09-07, also
stated the ruff baseline as 37 against a gate of 35. The claim that cost ten red tests was still being handed to
new sessions. The failure is one level up from the one recorded above: the correction was
applied to a list someone REMEMBERED, not to a set someone SWEPT. Correcting the copies
you can think of is not correcting the rule either.

**AND THE SECOND COPY CAN BE INSIDE ONE FILE, which no sweep of FILES can see.**
Both corrections above widened the SET OF FILES to sweep. MEASURED 2026-09-09:
`db_reclaim.py` stated its `job_runs` retention window in THREE rationale blocks —
the `#:` comment on `_RUN_HISTORY_RETENTION_DAYS`, the docstring of
`_prune_run_history` which performs the DELETE, and the module docstring of
`tests/scheduler/handlers/test_the_run_history_is_finally_bounded.py`. `c628d1bf`
tightened it on Bakir's authority and rewrote the first. The other two went on
saying the window deleted nothing and that tightening it had been escalated to him
rather than taken — while eleven passes deleted 3,909, 4,087 and 4,151 of his rows.
**A reader auditing what this platform deletes would have read the method that does
the deleting and concluded it deletes nothing.** The cure is to REMOVE the extra
copies, never to sync them; syncing leaves the trap armed for the next change.
`scripts/superseded_constants.py` now finds prose stating a value its own constant
no longer has, in `src/` and in the `tests/` files that import it. Only EIGHT
constants in this tree have ever held a superseded value, so the class is small and
is now swept rather than remembered.

**`git log -S` DOES NOT FIND A VALUE CHANGE — use `-G`.** `-S` reports commits where
the COUNT of a string changed, so for `NAME = <number>` it sees the commit that
INTRODUCED the constant and is blind to every commit that only changed its VALUE.
Against `_RUN_HISTORY_RETENTION_DAYS`, `-S` returns one commit and `-G` returns two,
and the one it misses is the change being traced. A constant edited twice hides its
middle value entirely. Found by a control test, not by reading: the scan still
reported the stale site, because the value it happened to need was the constant's
first. **A query that returns the right answer for the wrong reason is the hardest
instrument error to see, because nothing looks wrong.**

**The sweep, so the next person runs it instead of counting:**

```bash
grep -rn -i "it hangs\|hangs on this box\|never.*bare .pytest\|full pytest.*hangs" \
  --include="*.md" --include="*.py" --include="*.sh" . \
  | grep -v "do_not_push_to_git_research_only\|/.git/"
```

It returns ~19 hits and **most are correctly left alone**: `_bmad-output/` and
`docs_archive_ralph_2026-06-30/` are dated records of what was instructed at the time, and
rewriting them would be falsifying history. The boundary is LIVE INSTRUCTION versus
RECORD — fix anything a future session will be told to obey; leave anything that documents
what a past session was told.

**A MEASUREMENT TAKEN ON THIS BOX WHILE THIS LOOP RUNS MAY BE MEASURING THE LOOP.**
MEASURED 2026-09-07, chasing "why does the platform restart ~80 times a day". It
does not, in any sense that describes the product: **715 boots over eleven retained
days, 504 of them (70%) CodeWatcher re-execing the core after a `src/` edit** — this
programme editing the instance it is measuring. On 09-07: 32 boots, 26 CodeWatcher,
and the 6 remaining match the `./start.sh` runs made by hand. In a deployment where
nobody edits `src/`, seven of every ten of those boots do not exist. The restart path
is fully instrumented at INFO the whole way — `[runtime] code change settled →
requesting core restart`, `[startup] core: exec-replacing with fresh code`,
`[startup] core: code watcher armed` — so this was always answerable.

**It took FIVE wrong instruments to get there, every one the same mistake: grepping
for a COMPONENT'S NAME instead of asking what it EMITS.** "CodeWatcher" appears in
neither of its own two decisive log lines. Along the way I "found" that the browser
runtime was killing the process (it was a 3.0s startup step my 1-second gap
heuristic split in half), and that `reconcile_owl_schedules` preceded every restart
(it is a boot step, 60ms from the rest). Each looked like a finding and each
dissolved when the RAW records were printed. This is the file's own rule about greps
returning zero, in the mirror: a grep returning a confident NON-zero is just as
capable of being about the wrong thing.

**PROGRESS PERCENTAGE IS NOT LINEAR IN TIME — do not extrapolate from it.** MEASURED
2026-09-04, and this note originally said the opposite because I did exactly that. A
run sat at 21% after 17 minutes; I extrapolated ~80 minutes, checked the box, found
it deep in swap (88 MB free, **2,373 MB swapped**, pytest at 35% CPU, I/O wait 11-13%
— RSS was pytest 1,880 MB + the live platform 1,223 MB + the agent session 732 MB on
a 7.6 GB Jetson) and wrote down that the swap was slowing the suite.

**That run finished in 1744.83s — 29:04 — the FASTEST of all five recorded runs**
(1885, 1784, 1776, 1756, 1745). The swap was real and its effect on runtime was
nil. The early directories are simply slower per test: `tests/db` alone is ~7
minutes for 102 tests, each replaying every migration, so the first fifth of the
progress bar eats a quarter of the clock.

Two things to keep from that. A crawling progress bar is the exact shape of the "it
hangs" belief that cost this programme ten red tests, so **check the process's
CPU-time delta, not the percentage** — and having checked, do not then invent a
prediction from the percentage anyway. A measurement of the BOX is not a
measurement of the RUN.

**Green is a floor, not a trophy: re-run it, because only it sees cross-test pollution.**
The last failure to fall was exactly that — a concurrency test that passed alone, passed
in its package, passed 5/5 on repeat, and failed once in 11,875 tests because it bet
50ms on the scheduler. It was also VACUOUS in the losing direction. No tripwire can
replace this run.

Interactively, targeted paths with timeouts are still right — but **a hanging test
is a failing test only after you have checked it is not merely slow.** `tests/db`
is minutes, not seconds; I recorded it as a hang at a 250s timeout and was wrong.

**Its mechanism, re-measured 2026-09-05, because this line used to state it
wrongly.** It said "102 tests, each replaying all 128 migrations". There are more than
that — read the live count from `ls src/stackowl/db/migrations/*.sql | wc -l`, because this
sentence has now been wrong twice — and the cost is not one replay per test but **46
explicit
`MigrationRunner(` sites inside `tests/db`**. That distinction matters to anyone
trying to make it faster: **`tests/_schema_template.py` already exists** — build the
schema once, copy it per test — and **120 files already use it**, while **22 files
outside `tests/db` still construct a real `MigrationRunner`**. Some of those are
migration tests that legitimately need the real thing; the rest are the cheap win,
and it is the built-but-not-wired shape rather than a performance mystery.

**A targeted run CANNOT see a cross-cutting guard — run `./scripts/tripwires.sh`.**
Targeted paths are picked by what the change looks related to, and a guard that
protects the whole repo looks related to nothing. Two defects shipped exactly that
way: `usage_report.py` read the owner-governed `task_outcomes` with no `owner_id`
predicate (that item ran `tests/tools/meta` and `tests/startup`; the tripwire lives in
`tests/tenancy`), and deleting six modules left three of their entries in the
owner-scope allowlist. The gate is **~4.5 minutes** — MEASURED across FIVE runs on
2026-09-08: `284/284/284/286/290 passed, 2 skipped` in 256.8s to 265.9s. It said
**~2 minutes** until then, from a 2026-09-06 reading of `138 passed` in 105.88s, so the
gate **doubled in two days** while the number stood still. It matters because a stale
duration is why a gate gets SKIPPED, and this one grows by construction: the marker is
the source, so every loop that ships a guard adds to the cost and nothing updates the
figure. Read it off your own run rather than trusting this sentence.
It runs everything marked
`@pytest.mark.tripwire` plus `progress_lint`, `ruff` and `mypy`. Mark a new guard with
that marker and it joins automatically — the marker is the source, not a path list.

**Only the FULL run finds cross-test pollution**, and it is not a hypothetical: every
red run above ended with failures whose directories pass cleanly in isolation
(`tests/mcp` 101, `tests/owls` 334, `tests/pipeline` 1687, `tests/parliament` 80). No
targeted path can ever see them. **Wait for a CONDITION, never a duration** —
`tests/_async_helpers.py::await_until` exists so the next such test is fixed by polling
what it actually needs instead of enlarging a sleep.

**`tests/<package>` NEVER runs `tests/*.py`.** 69 test files sit directly in `tests/`
— consent, audit chains, the SSRF guard, capability profiles, migrations — and no
package path touches one of them.

**AND KNOWING THAT DID NOT HELP, so stop reading it and run this instead:**

```bash
uv run python scripts/tests_touching.py            # what you changed
uv run python scripts/tests_touching.py src/…/x.py # or name the files
uv run python scripts/docs_touching.py             # and which DOCUMENTS declare it
```

**The third line is the same lesson one surface over.** This file says the design
document is the fifth surface of a change, and it said so while `1897e0c3` — a
one-line log-level promotion in `commands/registry.py` — left D14.1 and D14.2 stale
for the next loop to find. MEASURED over the last 60 commits: **15 touched a path
some document declares as its `Source`**, one in four, `bf603ef7` reaching NINE of
them. Two whole loops went on draining staleness that earlier loops created. The
re-reading is real work and cannot be automated; doing it on the loop that made the
change, while you still remember whether it concerned the document, can be.

It names every test that imports OR NAMES each changed module — the string-mention
half is not optional here, because this tree monkeypatches by dotted name and reads
`inspect.getsource` of modules it never imports — marks the root-level ones with `*`,
and prints the ready-made `pytest` line. MEASURED 2026-09-08: the paragraph above was
in this file and I had read it, and DEBT-228 still shipped with a red
`tests/test_story_7_1b.py` that a `tests/scheduler` run could not see. **A warning
names the HAZARD; the reader needs the ANSWER.** Against `scheduler/scheduler.py` the
tool returns 49 files and marks exactly one — that one.

**Restart with `./start.sh`, then verify via `~/.stackowl/logs/stackowl.jsonl`, never a
PID.** A deletion is not live until the process holding the old code is gone. Check the
core's start time against your last commit before believing anything you measure.

## Loop-oriented, and never a second engine

**Bakir's standing rule, 2026-08-17: everything the platform does is a TASK on ONE loop,
and no implementation may duplicate logic or code that already runs work.**

Every trigger is a task — a chat question, a scheduled run, a sub-goal an agent creates for
itself. One table, one loop, claimed atomically and run in PARALLEL (five pending rows =
five concurrent workers, no ordering). A failure returns the row to pending *with what
failed*, so the next attempt is constrained rather than blind.

**A task is complete when its outcome reached its DESTINATION, not when the function
returned.** Ask a question on Telegram and the task is done only once the answer is
delivered there. Every task therefore carries a destination and an achievement condition.
This is the same rule as "measure the EFFECT" below, applied to work instead of tools.

**Before building anything that runs, retries, schedules or tracks work: find the existing
loop and extend it.** This rule is earned — the tree already accumulated FOUR overlapping
engines: `tasks` (live), `retry_queue` (live), `objectives`/`objective_subgoals` (~2,500
lines, driver firing every 60s; live, see above), and `job_queue` (**zero references
anywhere in `src/`**). Never add a second queue, a second retry path, or a second status
column. Sub-tasks are rows with a parent and `depends_on`, so a graph is edges between
rows — not a second system.

The one place the claim-and-dispatch is already correct is `scheduler.py`: `asyncio.gather`
over due rows behind a CAS claim (`UPDATE … SET status='running' WHERE status='pending'`)
so concurrent dispatchers can never double-run. Copy that shape; do not invent another.

## Logging

Named loggers via `stackowl.infra.observability` (`log.tool`, `log.engine`,
`log.memory`, `log.gateway`, …), 4-point on every `execute()`, and every `except` logs.

**Production runs at INFO. A `log.*.debug` line does not exist when you need it.** This
is not a style note: D08.1's fourth acceptance check sat open for days because its only
evidence line was DEBUG, and no volume of live traffic could ever have closed it. If a
log line is the evidence for a claim, it must be INFO — and run the query that would
close the claim *before* you need it, to confirm it returns something.

**MEASURED 2026-09-09, and the number is starker than the rule sounds: 652,309 INFO,
13,547 WARNING, 11,250 ERROR, 2 CRITICAL and ZERO DEBUG across 677,108 retained
records.** Not "rarely useful" — a `log.*.debug` line has never once been written by
this deployment.

**AND WRITING THE RULE DOWN TWICE DID NOT ENFORCE IT.** It is here and in
`item-loop/SKILL.md`, and TEN records in `progress.yml` each carry a repair of
this exact shape — DEBT-2, 145, 219, 240, 252, 256, 260 and ESC-70/71/73 (the last three
are session journals, one instance apiece) — the latest within days of this line. Ten symptom fixes, no cause fix, which is this file's own "two copies of one
rule" shape applied to itself. The cause: 4-point logging assigns a level BY POSITION
(entry and exit are DEBUG) when the level belongs to whether the branch is an OUTCOME
someone must be able to see.

**So it is now a gate, not a paragraph:**

```bash
uv run python scripts/logging_visibility.py          # the 5 background packages
uv run python scripts/logging_visibility.py --all    # every package, reported not gated
```

It names any function that logs loudly on one return path and only at DEBUG on another —
so its outcome is visible exactly when it ACTS and invisible when it declines, which
inverts what a reader needs. `tests/audit/test_a_background_subsystem_that_declines_still_says_so.py`
ratchets the background set and fails on a new one BY NAME (and on a stale exemption, so
the allowlist cannot rot). It found the shape in the self-healing loop itself:
`route_rca_verdict` declined at DEBUG and consumed at INFO, so its last INFO line was
2026-09-03 while `incident_escalation: RCA complete` fired 41 more times — a whole
session could not tell a DEAD self-healing loop from a DECLINING one. Nine other sites
were the platform confessing a MISSING COLLABORATOR (`no db wired`, `no skill curator
wired`, `no embedding registry`, `flag off — noop`) — the built-but-not-wired family
this file calls the commonest defect here, reporting itself where nobody could read it.

Scoped to `scheduler/`, `parliament/`, `learning/`, `notifications/` and `objectives/` on
purpose: a per-turn tool that returns quietly is still observed, because its turn has a
user, a reply and a cost record. A scheduler tick has none of those.

## Measuring is the job, and the instrument lies too

**Check what a denominator is MADE OF, not just that it is non-zero.** "0 exemptions
over 7 browser calls" looked like a failure and was 0-over-0: all seven were
read-severity tools that never reach the branch. A zero numerator over a zero
denominator is not a pass either.

**A test that passes immediately may be vacuous.** Three round-trip tests passed
because `get()` uses `SELECT *` and picks up new columns for free; the LOOP's
`claimable()` builds an explicit column list and returned `None`. Test the path
production takes.

**A fixture that cannot show the bug proves nothing.** A fake page with no
`close()` made eviction "pass" through its own except branch.

**Log greps: the field is `"msg": "` WITH A SPACE after the colon**, and the inner
key is `fields`. A regex without the space returns empty against a 14MB file — four
false negatives in one session before a control on a known-present string exposed
the instrument rather than the system.

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

**AND `jq` IS A THIRD CASE, which looks like the worst of them and is the mildest.**
MEASURED 2026-09-08 while re-running D05.8's Verification section: every `jq` over
the glob prints `jq: error (at …/stackowl-2026-09-05.jsonl:41431): Cannot index
number with string "ts"` — the same NUL block that truncates `/usr/bin/grep`. It
reads exactly like a query that died half way, and I believed that for three
commands. **It does not truncate.** Counted with and without that file,
117,960 + 49,182 = 167,142 exactly: `jq` reports the bad line on stderr and
processes every other one. So the error is noise and the numbers are whole — the
hazard here is DISTRUSTING A COUNT YOU SHOULD HAVE BELIEVED, which is the mirror of
every other entry in this section. Do not add `2>/dev/null` to hide it either: that
would also hide a real parse failure. Read the count, and check the arithmetic if it
matters.

**In SQL `LIKE`, `_` is a WILDCARD.** `skill_name LIKE 'incident_%'` returned 1 row
and the row was `incident-evidence-brief` from six weeks earlier — the underscore
matched the hyphen. It was about to close an acceptance check that had not fired.
Use `LIKE 'incident\_%' ESCAPE '\'`, and treat any `LIKE` over a name that CONTAINS
`_` as wrong until proven otherwise. Same family as the `"msg": "` space.

**A check must watch the branch that EMITS the line, not the event the prose names.**
D04.5's open check said a decline line "needs a tool breaker to open, and there have
been zero such events in five days". Three had opened since the fix shipped, one on the
shipping day — and the open is not the trigger anyway: the emitter sits behind a
re-dispatch guard, and **52 opens produced 12 of those**. A reader watching the named
event would have seen it fire and concluded the fix was broken. Find the `log.*` call,
read what guards it, name THAT. Same check also grepped `stackowl.jsonl`, one file,
while the same document's body cited a count "across every kept log" — a midnight-blind
query returns 0, and 0 reads as *not yet*.

**A log-reading check becomes a RECORD when its evidence rotates, and then reads 0
forever.** MEASURED 2026-09-07: four Verification commands in three documents cite
results from before the oldest retained log — one from 2026-07-27 against logs starting
2026-08-28 — so re-running them returns 0 and 0 reads as failure. Read the horizon from
the FILES, not the retention setting: `backupCount` is 30 and nothing is due for
deletion, yet only ten dated files exist. Keep something runnable beside every dated
log measurement.

**Count incidents, not log lines.** "19 database-is-locked events" was 19 LINES; one
contention moment emits four.

**AND CHECK THE MESSAGE IS STILL ONE THE CODE CAN WRITE — AND THAT IT STILL DOES.**
The corpus keeps every message a DELETED line ever wrote, and nothing marks it deleted,
so a grep returns a confident count for behaviour that cannot happen again. MEASURED
2026-09-09 over 13 retained files: of **328** distinct WARNING/ERROR/CRITICAL messages,
**47 are CURRENT, 244 are DORMANT and 37 are RETIRED** — only 47 describe what the
platform is doing NOW, so five greps in six land on something already answered.
**DORMANT is the one that fooled the instrument built for RETIRED**, on its very next
use: `compute_next_run: cron parse failed` is emittable, has 326 hits, and has not
fired since 2026-08-31 because `scheduler.py` learned to ask `_is_recurring` — whose
comment already rejects the tightening the log invites. One of them —
`[pipeline] deliver: no registry in services — discarding responses`, 153 hits over 12
days — reads as the platform discarding answers and cost this loop three
investigations before `4f3caf19` turned up, which had already split that branch; the
WARNING that means a real loss has fired ZERO times. Run
`uv run python scripts/retired_log_messages.py` before treating a log count as current
behaviour. It is a REPORT, not a gate, and its own number moved twice before it was
right: whole-literal matching calls every f-string retired, and JoinedStr-parts still
calls every `%s` call retired, because the placeholder lives inside the literal.

**Print the real shape before you filter it.** Seven times in one session a search
returned a confident wrong answer because it matched something other than the thing:
`discover` is a MODE of `session_search`, not a tool, so "0 invocations" was reported
for something with 9; the cost fields are `mode`/`turns`, not `action`/`rows`, so
"no instrument exists" was reported about a perfectly good INFO line; a scan of
`subscribe("literal")` nearly recorded "the budget-alert path is dead" when the
bridge subscribes three events in a LOOP. **A grep returning zero proves the pattern
did not match. It does not prove the thing is absent** — and this codebase wires
things dynamically, so static reads of it are wrong by construction. Dump one raw
record, or the whole field list, before believing any count built from a guess.

**An empty table is a QUESTION, not an answer.** `committed_facts` had 0 rows, so
"the archive has no writer" was reported and curated-only search was RECOMMENDED —
which would have made 361 real memories permanently unreachable and called it a
cleanup. They were one table over: `staged_facts`, 361 rows, embeddings populated,
newest written minutes earlier, behind a promotion step that never runs. The
sibling of the rule above: measure the EFFECT, never trust the CALL — and never
trust the EMPTY TABLE either. Before concluding a store is dead, find its writer
and ask where the writes went.

**A scripted edit across many files needs a SYNTAX gate as its first check.** A
regex that inserted an import across 110 test files put two of them INSIDE a
multi-line `from ... import (`, and the first repair then inserted after a
FUNCTION-LOCAL import because it matched `lstrip()`ed lines. `pytest --collect-only`
over the whole tree and `ast.parse()` found both in seconds; a test run would have
found them late and noisily. Cheap gate first, then tests.

**A scripted edit REPLACES A SPAN; it never inserts at an offset.** MEASURED
2026-09-07: an insertion at "the next newline after the anchor" landed INSIDE a
sentence, because in a hard-wrapped paragraph a newline is not a sentence boundary —
and it garbled `SKILL.md`, the file the loop reads on every invocation. The read-back
was `grep -c` on the inserted number, which returned 1: that proves the text landed and
nothing about WHERE. Name both ends (`assert old in s` then `replace(old, new, 1)`) and
read back the SPAN, not a count.

**Never build a commit message through `printf`.** A `%` in "36%" was read as a
format specifier and silently truncated the message mid-sentence, losing the
paragraph that mattered — and the commit was already pushed, where rewriting
history is banned. Use a heredoc into a file and `git commit -F`. In this
programme the record IS the deliverable.

**Sweep EVERY engine before claiming silence.** Parking `tasks` looked like it
stopped a runaway; `retry_queue` and `objective_subgoals` were still armed and the
user was still being messaged hours later.

**A gateway-side fix is not live until `./start.sh`.** CodeWatcher exec-replaces the
CORE only. A heartbeat fix sat dead for an hour while being reported as shipped.

## The shapes that account for nearly every real defect here

1. **A write with no reader**, or an actuator wired on only some paths. Measure the
   EFFECT, never trust the call.
2. **Test doubles that stopped resembling the real thing.** Generate fixtures from the
   same constants the code uses where you can.
3. **Two copies of one rule.** One source; have the other ask it.
4. **No decay.** Anything that only appends will poison its reader. And when you remove
   a writer, ask what was *bounding* — or *triggering* — the thing it fed.
5. **Built but not wired.** The capability exists, works, and nothing calls it.
   `stackowl.supervisor` did backoff-restart and escalation while every channel
   receive loop ran as a bare task. `idempotency_key` was stored, read back, and
   had no unique index. `committed_facts_fts` still indexes 1,112 rows of content
   that no longer exists because its writer was removed. **A feature ships ON: if
   nothing sets the flag, you shipped decoration** — D03.4's result cap went out
   with no tool declaring one and could never fire. **That is now FIXED, and the
   correction is the point:** re-measured 2026-09-04, `browser_extract` declares
   `_max_result_chars = 200_000` and the cap has FIRED — four INFO lines on
   2026-08-29, `original_len 1215049 -> cap 200000, cut 1015049`. A landmine note
   that stays after the mine is cleared sends the next reader hunting a defect
   that no longer exists, which this file has already cost once.
6. **Deleting a row while its writer lives.** Migration 0125 deleted the
   `retry_sweep` job at 00:31:02; scheduler assembly re-seeded it at 00:31:33.
   Every boot, for thirty-one seconds. Remove the writer, not the row.
