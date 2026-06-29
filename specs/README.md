# Specs — Ralph Wiggum harness

The canonical spec for the active work (the persistence / never-give-up arc) is
**not duplicated here** — it lives on disk as the Ralph loop's shared state:

- **Plan + story checkboxes (truth):** [`.ralph/PERSISTENCE_IMPLEMENTATION_PLAN.md`](../.ralph/PERSISTENCE_IMPLEMENTATION_PLAN.md)
- **Per-iteration driver prompt:** [`.ralph/PERSISTENCE_RALPH_PROMPT.md`](../.ralph/PERSISTENCE_RALPH_PROMPT.md)
- **Loop driver:** [`scripts/ralph-loop.sh`](../scripts/ralph-loop.sh)

## Run

```bash
./scripts/ralph-loop.sh        # up to 12 fresh-context iterations
./scripts/ralph-loop.sh 4      # cap iterations (controls cost)
```

Each iteration spawns a fresh `claude` process, picks the FIRST unchecked story
in the plan, implements + verifies + commits + pushes it, marks it done, and
stops. The loop ends when the agent emits
`<promise>ARC-A-PERSISTENCE-COMPLETE</promise>` (only when genuinely true) or
`MAX_ITER` is hit. All output is captured under `logs/`.

## Acceptance criteria (remaining for arc-complete)

- [ ] **PA5(b)** silent-delivery gate — assert a durable NACK in the STORE for the
      state that SHOULD nack (define it first: F-62 leaves handler-not-registered
      jobs *pending*, NOT dead-lettered — do not conflate pending / failure-ledger /
      quiet-hours-deferred / dead-letter).
- [ ] **cronjob** `post_condition` reading the JobScheduler back (create/watch
      actions) → remove `cronjob` from `_KNOWN_UNVERIFIED` (ratchet self-policing
      test enforces the removal).
- [ ] **Live re-test** — never-give-up scenario on the running server + boot-green
      census (needs the box; models are remote).

Done stories (PA0–PA4, PA5a) are recorded with commit hashes in the plan.
