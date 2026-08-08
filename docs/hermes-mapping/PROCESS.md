# How We Learn From Hermes And Fix A Component

This is the method. `HERMES_STACKOWL_MAP.md` says *what* to fix, `progress.yml` tracks *where we
are*, `DOC_STANDARD.md` says what each component's document looks like — **this file says how the
work is done.**

Read it before picking up any item. It is short on purpose.

---

## The premise

Hermes Agent is the **teacher platform**. It is popular and it works, so where it and StackOwl
disagree, the default assumption is that Hermes is right and we should understand why before
disagreeing. That default is rebuttable — 16 of the 111 mapped items are ones where StackOwl is
genuinely ahead — but it must be rebutted with evidence, not preference.

Two rules sit above everything:

**Law 1 — per-conversation prompt caching is sacred.** Nothing mutates past context, swaps
toolsets, or rebuilds the system prompt mid-conversation. Only compression may.

**Law 2 — the core is a narrow waist; capability lives at the edges.** Every model tool ships on
every API call. Grow the product aggressively, at the edges, never at the waist.

And one decision procedure, the **Footprint Ladder** — take the highest rung that solves it:
extend existing code → CLI command + skill → service-gated tool → plugin → MCP server →
new core tool (last resort).

---

## Port the design, not the code

We are learning from Hermes. **We are not copying Hermes.** Every item does this:

1. **Read their implementation until you can explain it to someone else.** Not skim — read. Their
   `docs/session-lifecycle.md` gave us the `session_key`/`session_id` split in an afternoon;
   grepping `gateway/session.py` for an hour would not have.
2. **Extract the idea, discard the code.** Write our own implementation, in our idiom, with our
   naming, against our seams.
3. **State the divergence.** Every design document carries a paragraph on where we deliberately did
   it differently and why. Without it, a future reader assumes divergence was an oversight and
   "fixes" it back.

Three real examples from D01:

| Hermes | StackOwl | Why we diverged |
|---|---|---|
| Prompt lives on a cached in-memory agent object | Prompt is **persisted** | Our core `os.execv`s itself on every code change; an in-memory cache would be discarded constantly |
| Session key is `platform:chat_type:chat_id` | Ours prefixes the **owl** | They have one agent; we have owls, and a different owl means a different prompt |
| Prompt stability enforced by reviewer discipline | Enforced by a **measured hash invariant** | We prefer an assertion to a convention |

---

## Avoid duplication, deliberately

This is the failure mode we are most exposed to: adopting a Hermes mechanism *next to* the
StackOwl mechanism that already does the job, and ending up maintaining both.

**Before writing anything, check `dedup_targets` (X1–X10) in `progress.yml`.** Ten are known. If
the item you are on carries a `dedup_target`, resolving it is part of the item, not follow-up work.

Three questions before any new module:

1. **Does StackOwl already do this under another name?** `TurnProgressSupervisor` and Hermes'
   `tool_guardrails` may be the same thing twice (X5). `objectives/` and Kanban may be two work
   queues (X1).
2. **Is the Hermes thing a superset, a subset, or a sibling?** Their `verification_evidence` is a
   strict *subset* of our `acceptance_authority` — so we keep ours and add nothing (X8). Their
   `error_classifier` is a *sibling* of our recovery ladder — so they compose, classifier feeding
   ladder (X9).
3. **Can one mechanism serve several items?** The `session.rollover` event from `D01.7` is the
   idle moment that `D09.1` (background review), `D09.3` (curator) and Q17's memory summary all
   need. Three items, one seam, three schedulers not built.

The best deduplication is architectural — noticing that two items want the same seam — not a
cleanup pass afterwards.

---

## How work is chosen — EVIDENCE-FIRST (Bakir, 2026-08-07)

The map says what is worth fixing. It does **not** say what to fix next.

Waves 1–3 were worked straight off the map. Everything after was driven by live
measurement and by what actually broke in front of the operator — phantom cloud
pricing on 82,016 rows, critical alerts lost to an unverified `cast`, a Like that
recorded the vote and left the buttons on screen. That work was right to do, and
none of it was on the map's critical path.

So the operating model is stated rather than drifted into:

**Priority comes from evidence — a measurement, a live failure, or an operator
report. The map is the CHECK, not the queue.**

### The one obligation that makes it safe

Evidence-first has exactly one failure mode, and it has already happened:
building something the map already had an item for. The skill-lifecycle curator
was designed, built, tested, validated and shipped before anyone noticed it was
`D09.3`, whose stated gap was almost word for word the problem being solved.

So, **before writing anything**:

```bash
uv run python scripts/map_check.py <words describing what you are about to build>
```

It prints every matching item with the three things that change what you do next:
whether it is already claimed, what it **depends on**, and its `dedup_target`.
Exit status is 1 when something matched, so it can gate a workflow.

If it matches, you are on a mapped item — run its seven stages and update
`progress.yml`. If it does not, you are doing evidence-led work; record it as
debt or an ADR so the record still says what we knew and what we chose.

This replaces "remember to check `dedup_targets`" with a command, because
remembering is the part that failed.

---

## The seven stages

Every item runs all seven. `no_change_needed` is a legitimate outcome; silence is not.
Update `progress.yml` **after each stage**, not at the end.

| Stage | What happens | Gate |
|---|---|---|
| **brainstorm** | 25 questions to Bakir, in rounds of four. Understand what Hermes designed and *why*, what StackOwl does today and *why*, then explore options. | Decisions recorded in the item's `decisions` |
| **architect** | Turn decisions into a design: seams, interfaces, migration, and which ladder rung this lands on. **Opens the document.** | Design doc exists, names the rung, names every file, states how Laws 1 and 2 hold |
| **implement** | Build it. Tests first. Minimal root-cause diffs. Ships ON, not dormant. | Feature complete |
| **cleanup** | Remove what this made obsolete. Resolve the item's `dedup_target`. | Nothing duplicated, nothing orphaned, lint + types green |
| **test** | Unit + gateway-driven integration from business requirements, mocking only the AI provider. Targeted paths with timeouts. | Green, no pre-existing failure left unexplained |
| **validate** | Prove it in the real platform. Restart, drive a turn, read the JSONL. | Evidence pasted into `notes` |
| **document** | Close the doc. Re-verify every path and default. Stamp date + commit. | All sections final, Verification passes, every Invariant maps to a test |

### The 25 questions

Bakir's standing instruction: **25 questions per item during brainstorm, and 25 more for each
architecture design.** Batched four per round. The purpose is to extract *his* vision, not to
confirm ours — so ask the questions whose answers would change what we build, and push back when
two answers contradict each other.

That contradiction-hunting is not optional. In `D01.1`, Q5 ("a stable picture of me") and Q12
("I won't give up per-message search") could not both hold as stated. Surfacing it produced the
actual design — a loaded profile *plus* an on-demand memory tool — which neither answer contained
on its own.

---

## Evidence, not assertion

The rule that has already caught the most: **never state anything you have not run.**

The first draft of `designs/D01.6.md` cited `sqlite3 ~/.stackowl/stackowl.db` and a test file. All
three facts were wrong — the live database is at `~/.stackowl/workspace/stackowl.db`, the `sqlite3`
CLI is not installed on this box, and the test file does not exist. The document would have failed
its own Verification section. It was caught by *running* the section rather than trusting it.

So:

- Every source path, config key, default, and line count in a document is verified against the tree
  at the stated commit.
- Every Verification section is copy-pasteable and has actually been pasted.
- "Tests pass" means you ran them and read the output. Paste the count.
- A stale document is worse than none, because it is believed.

---

## When implementation disagrees with the design

It will, and that is fine — provided it is **visible**.

`designs/D01.6.md` said thread five values through provider signatures. Implementation found that
`_record_cost` is a single site already reading `trace_id` off `TraceContext`, which also carries
`session_id`. The better seam meant no provider signature grew at all.

The rule: **say so in the commit, and correct the document in the `document` stage.** What is not
allowed is the design and the code quietly diverging until nobody knows which is true.

---

## Documentation is tracked for every finding

All 111 items carry a `document` stage and a `doc` field in `progress.yml`. No item reaches `done`
without a document at `docs/hermes-mapping/designs/<ID>.md` meeting `DOC_STANDARD.md`.

Our template takes Hermes' structure — audience, source files, field tables, flow diagrams, config
appendix — and **adds four sections they had no need for**: Invariants, Observability, Failure
modes, and Verification. Hermes has no trace propagation, no mandated logging contract, no
acceptance authority and no self-heal ladder, so their documents never had to describe those. We
have all four, and they are the parts of StackOwl worth keeping. A document that omits them
describes half the system.

Where several items land on one subsystem, the later ones **extend** the existing document rather
than adding a second. That is the same anti-duplication rule applied to prose.

---

## Standing engineering rules

Not negotiable, from Bakir's long-standing preferences. The full list is `rules` in `progress.yml`.

- Check existing code before writing new. Wire and extend, never recreate.
- Fix the architecture, not the example that surfaced it.
- Minimal diffs. Only the lines needed.
- Never disable or stub an existing feature without explicit consent.
- No hidden errors. Every catch logs. Recover loudly or propagate.
- 4-point logging (entry / decision / step / exit) on every new `execute()`.
- Schema changes are idempotent migrations, never hand edits.
- All state under `~/.stackowl/` via `StackowlHome`.
- **No vendor-specific logic.** Dispatch on shape and capability, never on a provider's name.
  Bakir runs a single LiteLLM gateway; his users run a hundred different backends. Code that
  branches on who the provider is will be wrong for one of them.
- No hardcoded English keyword lists. Cross-platform. Runs on all hardware.
- Finished features ship ON by default, not dormant behind a flag.
- Restart the platform after every fix and verify via the JSONL log, not a PID.
- Targeted test paths with timeouts. Never a full `pytest` run — it hangs on this box.
- Commit at sub-story granularity when green. Bisectable.
- Never push `do_not_push_to_git_research_only/`.

---

## Known debt is tracked, not ignored

Pre-existing red is not out of scope by default. When we choose not to fix something now, it goes
in `known_debt` in `progress.yml` with what it is, the evidence, the decision, and why. Currently:
`DEBT-1` — 46 pre-existing `ruff` errors in `src/`, in files unrelated to any current item, not
fixed mid-item because that would violate minimal-diffs.

The test is simple: could someone reading `progress.yml` in six months tell what we knew and what
we chose? If yes, it is tracked. If it only exists in a chat log, it is lost.
