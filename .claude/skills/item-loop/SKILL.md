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
evidence cannot settle it, the panel's recommendation, and what is blocked until it is
answered. The operator clears these in one sitting. **Continue the item on everything not
blocked.**

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
- **test** — targeted paths with timeouts. **Never a full `pytest` run — it hangs on this
  box.** A hanging test is a failing test. Pre-existing red is in scope: root-cause it, fix
  it, and say so.
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
