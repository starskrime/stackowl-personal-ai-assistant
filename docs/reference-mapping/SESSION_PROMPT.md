# Session kickoff prompt

Copy the block below into a new Claude Code session in this repo. It re-establishes the
working method without needing the previous conversation.

---

```
We are rebuilding StackOwl against a reference architecture. Read these three files
first, in this order, before doing anything else:

  1. progress.yml                              — state of record. `current` says where we are.
  2. docs/reference-mapping/PROCESS.md            — how the work is done. This is the method.
  3. docs/reference-mapping/DOC_STANDARD.md       — what every document must contain.

The teacher platform is Hermes Agent, cloned READ-ONLY at
do_not_push_to_git_research_only/hermes-agent (gitignored, never push it). Where Hermes
and StackOwl disagree, assume Hermes is right until evidence says otherwise — but 16 of
the mapped items are ones where StackOwl is genuinely ahead, so that default is
rebuttable. Port the DESIGN, never the code: read their implementation until you can
explain it, write our own in our idiom, then state the divergence in the design doc.

USE SUPERPOWERS SKILLS. Not only when they obviously apply:
  - superpowers:brainstorming            before any design work on an item
  - superpowers:writing-plans            to turn decisions into an implementation plan
  - superpowers:test-driven-development  tests before implementation, always
  - superpowers:systematic-debugging     for any bug, before proposing a fix
  - superpowers:verification-before-completion  before claiming anything is done
  - superpowers:requesting-code-review   before merging
  - tonyStyle                            on every task that touches StackOwl code
Announce which skill you are using and follow it.

HOW WE WORK, per item:

  Seven stages, none skipped: brainstorm → architect → implement → cleanup → test →
  validate → document. `no_change_needed` is a valid outcome; silence is not. Update
  progress.yml after EVERY stage, not at the end. Validate progress.yml BEFORE
  committing and let a parse failure block the commit.

  BRAINSTORM = ask me 25 questions, batched 4 per round via AskUserQuestion. Ask the
  ones whose answers change what you build. Hunt contradictions in my answers and put
  them back to me — do not silently reconcile them. Ask me 25 more for each
  architecture design.

  Before writing any new module, run the three-question dedup check in PROCESS.md and
  look at `dedup_targets` (X1-X10) in progress.yml. The best deduplication is
  architectural — noticing two items want the same seam — not a cleanup pass after.

  Never state anything you have not run. Verify every path, config key, default and
  test file against the tree. If a document's Verification section cannot be pasted
  into a terminal and produce a yes/no, it is not finished.

  When implementation disagrees with the design, say so in the commit and correct the
  doc in the `document` stage. Quiet drift is the one thing that is not allowed.

STANDING RULES (full list in progress.yml `rules`):
  - Check existing code before writing new. Wire and extend, never recreate.
  - Minimal diffs. Fix the architecture, not the example that surfaced it.
  - No hidden errors — every catch logs. 4-point logging on every new execute().
  - No vendor-specific logic: dispatch on shape and capability, never a provider name.
  - Schema changes are idempotent migrations only.
  - Finished features ship ON, not dormant behind a flag.
  - Targeted test paths with timeouts — never a full pytest run, it hangs on this box.
  - Restart with ./start.sh and verify via ~/.stackowl/logs/stackowl.jsonl, not a PID.
  - Pre-existing failures are tracked in `known_debt`, never silently ignored.
  - Never autonomously "fix" a failing test to make your change pass — stop and tell me.
  - Commit at sub-story granularity when green. Merge to main and push when done.

Start by telling me what progress.yml `current` says, then continue from there.
```

---

## What `current` says right now (2026-07-25)

- **Item:** `D01.7` — session lifecycle, stage `implement`, slices 1 and 2 done
- **Next:** slice 3 — gateway wiring. It touches the live message path, so it is its own slice.
- **Done:** `D01.6` (all seven stages, baseline captured)
- **Unblocked by:** the `D01.6` baseline — 5 messages, $2.85, 10 distinct prompts, 0 cached
