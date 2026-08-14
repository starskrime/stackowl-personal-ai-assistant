---
description: Work the current reference-mapping item through all seven stages, panel-answering the brainstorm
argument-hint: "An item ID (e.g. D08.2), or nothing to take progress.yml `current`"
---

Invoke the `item-loop` skill for $ARGUMENTS.

Take the item named, or the one in `progress.yml` `current` if none was given. Work it
through all seven stages. Answer the brainstorm with the six-lens panel, escalating only
what evidence cannot settle. Update `progress.yml` after every stage and run
`scripts/progress_lint.py` each time.

## Running it on a loop

Self-paced — the model decides when to come back, which suits stages of very different
lengths:

```
/loop /item-loop
```

Fixed interval, when you want a predictable cadence:

```
/loop 30m /item-loop
```

Both keep going across items: when one finishes, `current` advances and the next run picks
up the next item. Stop it with `/loop stop`.

## What it will not do without you

It parks these in `current.ESCALATIONS` and carries on with whatever they do not block:

- removing or disabling anything user-facing
- a destructive migration or any data deletion
- editing the code block shared with consent or clarify while that smoke suite is red
- a fourth attempt at a problem that has already failed three fixes
- a panel answer that contradicts a decision already recorded

Clear the queue in one sitting; the loop resumes on what it unblocks.
