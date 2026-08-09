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
> **Source:** `src/stackowl/<path>` (~N lines)
> **Config:** `<section>` in `stackowl.yaml`
> **Last verified:** YYYY-MM-DD, against commit `<sha>`

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
6. **`Last verified` is a date and a commit.** Not "recently". Re-verify when you touch the
   subsystem; if you cannot, move the status to `stale` rather than leaving a confident lie.

---

## Anti-patterns

- **Restating the code.** If the document is a prose rendering of the function body, delete it
  and improve the naming instead.
- **A field table with no defaults.** The default is the thing people actually need.
- **A lifecycle section with no priority order.** Two conditions that can both fire is a bug
  waiting to happen; the doc must say which wins.
- **Verification that says "run the tests".** Name the command and the expected output.
- **Marketing.** No "powerful", "seamless", "comprehensive". State the capability.
