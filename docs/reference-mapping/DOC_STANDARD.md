# StackOwl Documentation Standard

Every item in `progress.yml` ships a document. This file defines what that document looks like.

**Why a standard at all.** Hermes' `docs/session-lifecycle.md` is the reason we could adopt their
session model in an afternoon instead of reverse-engineering it for a week. A subsystem that is
only legible by reading its source is a subsystem that gets rebuilt by the next person who touches
it. The document is not overhead — it is what makes the next item cheaper.

---

## Our style is not their style

Hermes' docs are excellent at **structure**: audience, source files, numbered sections, field
tables, ASCII flow diagrams, a config appendix. We take all of that.

But their docs stop at "how it works". They have no trace propagation, no mandated logging
contract, no acceptance authority, and no self-heal ladder — so their docs never needed to
describe those things. **We have all four, and they are the parts of StackOwl worth keeping.**
A document that omits them describes half the system.

So our template adds four sections theirs does not have: **Invariants**, **Observability**,
**Failure modes**, and **Verification**. That is the difference between a document that explains
a design and one you can operate, debug, and prove.

---

## Template

Copy this shape. Sections are mandatory unless marked optional. Scale each section to its
subject — a two-line Invariants section is fine if there are only two invariants; padding is worse
than brevity.

````markdown
# <Component Name>

> **Status:** design | building | live
> **Map item:** D01.7
> **Source:** `src/stackowl/<path>`, `src/stackowl/<other>`
> **Config:** `<section>` in `stackowl.yaml`
> **Last verified:** YYYY-MM-DD, against commit `<sha>`
> **Reviewed:** `<short sha>`[, `<short sha>`] — why it does not apply   (optional)

## Why this exists

The problem in two or three sentences, and what goes wrong without this component. Name the
real incident if there was one. A reader who disagrees with the design should at least
understand the pressure that produced it.

## Model

The concepts and how they relate. One table per concept, fields with types and defaults.
If two identifiers exist that a reader could confuse (`session_key` vs `session_id`), open
with a table distinguishing them — that confusion is the single most common source of bugs.

## Lifecycle

State machine or flow. Use a fenced ASCII diagram, or Mermaid where the graph is genuinely
a graph. State the **priority order** explicitly wherever several conditions can fire on
the same event — ordering is behaviour, and ordering is what regresses.

## Invariants

Numbered, testable statements of what must never break. Each one is phrased so it could
become an assertion.

> I1. A session's `session_key` never changes for the life of the lane.
> I2. A reset always mints a new `session_id`; it never reuses one.
> I3. A session with an active background process is never expired.

Every invariant here should map to a test. If it cannot be tested, it is a wish, not an
invariant — either sharpen it or delete it.

## Configuration

Every key, its type, default, and range. Say which are safe to change live and which need
a restart. If a setting exists only as an internal bridge, say so and point at the
user-facing key instead.

## Observability

How to see this working, in production, at 2am.

- **Log lines** — the exact `msg` strings at entry / decision / step / exit, with the
  `_fields` each carries.
- **Trace spans** — span names emitted, and what they wrap.
- **A copy-pasteable jq query** that answers the most common question about this component.

```bash
cat ~/.stackowl/logs/stackowl.jsonl | jq 'select(.msg | startswith("session.")) | {ts, msg, key: .fields.session_key}'
```

## Failure modes

A table: what breaks, how it is detected, what it does about it, and what the user sees.
Say plainly which failures self-heal and which surface. "Fails closed" and "fails open" are
both acceptable answers — silently failing open is not.

| Failure | Detected by | Recovery | User sees |
|---|---|---|---|

## Verification

The commands that prove this works, with the output that counts as pass. This section is
what the `validate` stage executes. If a reviewer cannot copy this section into a terminal
and get a yes/no, it is not finished.

**Expect a SHAPE, never a count of something that can happen again.** A running platform
grows its logs, its sessions and its tool uses, so `expect: 629` is true on the day it is
written and false afterwards — and a Verification section that fails sends the next reader
hunting a defect that does not exist. MEASURED 2026-09-06: of the numbers pinned over a log
or a live table across these documents, **two were already false** — D09.1 expected
`never=16` against a real 15, and D12.3 expected `telegram 107 / cli 13` against 110 / 14.
Both had been correct when written, days earlier. This is the fourth instance of the same
defect (D10.3 637→648, D06.3 813→814, D13.4 629→640, now these).

D09.1 is the one worth remembering: its expectation went stale because a never-invoked tool
was **finally invoked** — so the pinned count would have reported a FAILURE for exactly the
event the item existed to encourage.

The stable forms, all of which say more than a count did:

* a **set** — "the channel set is exactly {rca, telegram, cli}, and nothing else";
* an **absence or bound** — "0 log lines for all three", "every refusal predates commit X";
* a **relation** — "0 ≤ never ≤ registered, and `all` names a non-zero never-invoked set".

Keep the number where it belongs: a **dated measurement** in the prose ("121 decisions in
nine days: 83 allow, 38 deny") is evidence and stays. Asserting that same number as today's
expected output is what goes wrong.

## Related

- Map items: `D01.1`, `D01.7`
- Docs: links to sibling documents
- Prior art: what we learned from Hermes here, and where we deliberately diverged
````

---

## Rules

1. **One document per map item** at `docs/reference-mapping/designs/<ID>.md`. When several items
   land on one subsystem, the later ones extend the existing document rather than adding a
   second — write the ID into the front matter's `Map item` line and cross-link.
2. **Written during `architect`, updated during `implement`, closed during `document`.** It is
   not a write-up produced after the fact; the design section IS the architect stage's output.
3. **Never state anything you have not checked.** Every source path, line count, config key, and
   default in a document must be verified against the tree at the stated commit. A stale doc is
   worse than none, because it is believed.
4. **Say where we diverged from Hermes and why.** Future readers will otherwise assume divergence
   was an oversight and "fix" it back. This is the single highest-value paragraph in most of
   these documents.
5. **Prose in the body, not comments in code.** If an explanation is long enough to need a
   paragraph, it belongs here with a one-line pointer from the code.
6. **`Source` names the PATH, never the size.** This template used to say
   `(~N lines)` and fourteen documents obliged. MEASURED 2026-09-06: THIRTEEN of the
   fourteen were wrong, one by 630 lines — `curated.py` was recorded at 531 and had
   grown to 1,161, so the document handed its reader a component half the real size
   before they opened it. A line count is a MEASUREMENT and rots every time the file
   is edited, which is the programme working; the path is a PROPERTY and does not.
   Same finding as DEBT-150's package-wide `expect: N passed`, one level up, and the
   fix has to be here rather than in the documents — a standard that prescribes a
   rotting field keeps producing them however many are corrected.

7. **`Last verified` is a date and a commit.** Not "recently". Re-verify when you touch the
   subsystem; if you cannot, move the status to `stale` rather than leaving a confident lie.

8. **`Reviewed:` names COMMITS, in backticks, or it is worth nothing.** It is the honest
   third answer when a commit touches a cited `Source` without touching a claim here:
   neither re-run the whole Verification section nor bump the date — record the sha and
   say why it does not apply. **It is MACHINE-READ.** `doc_check.py` extracts
   ``` `[0-9a-f]{7,40}` ``` and treats only those commits as dismissed, and
   `tests/audit/test_a_reviewed_commit_is_a_real_commit.py` checks that each one really
   touched a cited source, is newer than `Last verified`, and carries a reason.

   MEASURED 2026-09-09, and this rule is written here because its absence produced the
   defect: of 22 documents carrying the field, TWO named the ITEM instead of the commit —
   D07.3 said `DEBT-261`, D14.4 said `DEBT-263`. Both extract to the EMPTY SET, so the
   documents stayed on the stale list while reading as answered to a person, and all
   three guards SKIPPED them, because the helper that feeds them keeps a document only
   `if shas`. A zero numerator over a zero denominator is not a pass. Writing the item id
   is the natural mistake — it is what the author has in mind, and until now this
   standard did not say the field existed, let alone that a script reads it. There is now
   a fourth guard for the empty case; this entry is the half a guard cannot supply, which
   is telling the author before they write it. Only one `Reviewed:` line per document —
   the header parser keeps the LAST, so a second silently discards the first.

---

## Anti-patterns

- **Restating the code.** If the document is a prose rendering of the function body, delete it
  and improve the naming instead.
- **A field table with no defaults.** The default is the thing people actually need.
- **A lifecycle section with no priority order.** Two conditions that can both fire is a bug
  waiting to happen; the doc must say which wins.
- **Verification that says "run the tests".** Name the command and the expected output.
- **Marketing.** No "powerful", "seamless", "comprehensive". State the capability.
