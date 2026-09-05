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

**Bakir, 2026-09-01: whatever is retired is DELETED — code, registration, tests
and job rows, in the same change. Never a dead seat, never empty scaffolding
"for later".** If you find dead code while working an item, delete it in that
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
  shipped. The gate takes ~40s and runs everything marked `@pytest.mark.tripwire`
  plus `progress_lint` and both baselines.

  **The full run does NOT hang — it takes ~30 minutes, and this line used to say the
  opposite.** MEASURED twice: `6 failed, 11440 passed in 1885.47s` (2026-09-01) and
  `10 failed, 11853 passed in 1775.99s` (2026-09-03). The false claim survived HERE after
  `CLAUDE.md` was corrected, and because this file is what the loop reads on every
  invocation, no invocation ever ran it — which is how TEN tests sat red, every one of
  them a retired thing whose tests stayed behind. "It hangs" reads as *impossible*, so
  nobody tries. Run `./scripts/full_suite.sh` (detached, stamped log) and collect it
  later; a foreground timeout kills it mid-run, which is all "hangs" ever was.

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
