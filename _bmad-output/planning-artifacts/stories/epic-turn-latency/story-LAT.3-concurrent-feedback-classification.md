# Story LAT.3: Run feedback classification concurrently with answer generation instead of serially blocking it

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the StackOwl pipeline,
I want feedback classification to run concurrently with the answer-generation call instead of blocking in front of it,
so that a fast-tier LLM round-trip for feedback detection no longer adds serial latency to every turn that follows an assistant reply.

## Acceptance Criteria

1. `feedback.run` no longer `await`s `FeedbackClassifier.classify(...)` to completion before the pipeline proceeds to `execute` — the classify call is started as a concurrent task at the same point it runs today.
2. When the classifier confirms a real feedback/reaction turn (`feedback_handled=True` path), the existing short-circuit behavior (`_short_circuit`, replacing the turn with a confirmation, `execute` skipping the tool loop) is preserved exactly — `execute` must observe the classify result before it starts streaming the first user-visible chunk, not after.
3. When the classifier is not yet resolved by the time `execute` would otherwise start streaming, `execute` awaits the in-flight classify task at that join point (not earlier) — net added latency is `max(classify_time, upstream-step-time)`, not `classify_time + upstream-step-time`.
4. The two existing cheap guards are unchanged and still run before starting the concurrent task: no-prior-render skip (`feedback.py:100-102`) and the `_PREFILTER_MAX_CHARS=200` long-message skip (`feedback.py:104-109`).
5. On a turn where the classify task is still running when the pipeline would otherwise exit early (e.g., an error path before reaching the join point), the task is not silently abandoned — it either completes and its result is applied, or is explicitly cancelled with a log line, never left as an untracked orphan task.
6. No regression to the classifier's existing behavior for confident reactions (positive-pin, tone notes) — this story only changes *when* the result is consumed, not what it classifies or how.

## Tasks / Subtasks

- [ ] Task 1: Add a task slot for the in-flight classification (AC: #1, #5)
  - [ ] Add a field to `PipelineState` (e.g. `feedback_classify_task: asyncio.Task | None`) to carry the started-but-not-yet-awaited classification across the `feedback` → `execute` step boundary
- [ ] Task 2: Start the classify call as a task instead of awaiting it inline (AC: #1, #4)
  - [ ] In `feedback.py::run`, after the two existing cheap guards, replace the direct `await classifier.classify(...)` with `state.feedback_classify_task = asyncio.create_task(classifier.classify(...))` and return without blocking
- [ ] Task 3: Join at the correct point in `execute` (AC: #2, #3)
  - [ ] In `execute.py`, at the last safe point before the first user-visible chunk streams, `await`/check `state.feedback_classify_task` if set; apply `feedback_handled`/short-circuit behavior exactly as today if the result says so
  - [ ] If the task hasn't resolved yet at that point, awaiting it there is expected and fine (still faster than today's serial placement, since the answer-prep work already happened concurrently)
- [ ] Task 4: Handle abandonment safely (AC: #5)
  - [ ] On any pipeline exit path that would skip the join point (error, early return), explicitly cancel `state.feedback_classify_task` if still pending, with a log line — never let it dangle untracked
- [ ] Task 5: Tests (AC: #1-#6)
  - [ ] A confident-reaction turn still short-circuits with the same confirmation behavior as before, verified end-to-end through `execute`
  - [ ] A normal turn's total wall-clock time (classify + answer-prep, mocked to deterministic durations) reflects `max(...)` not `sum(...)`
  - [ ] The two existing cheap-guard skip cases (no prior render, long message) still skip starting the task at all — assert the task is never created in those cases (don't just assert classify wasn't awaited)
  - [ ] An error path that exits before the join point cancels the pending task and logs it — no unhandled task warning/orphan

## Dev Notes

- **Root cause:** `feedback.run` sits at `registry.py:43`, immediately before `execute` (the answer-generating step) in the pipeline step registry. `feedback.py:111-115` calls `classifier.classify(...)` and blocks until it returns. `FeedbackClassifier.classify` (`feedback_classifier.py:167-239`) resolves the fast-tier provider (`get_by_tier("fast")`, line 197) and does a live LLM round-trip (`provider.complete(...)`, lines 211-223) — this is the `[openai] complete → llm-gateway.dev.nera.gov:4000` RTT observed in the log, serially in front of every answer.
- **Existing guards, unchanged by this story:**
  - `feedback.py:100-102` — no prior assistant render → skip entirely. A first-turn "hi" is already free; the cost only shows up on a turn *after* the assistant has replied.
  - `feedback.py:104-109` (`_PREFILTER_MAX_CHARS=200`) — skips long messages (a long message is assumed to be a new task, not a reaction). Gap this story does NOT close: this only guards the *long* direction — short non-reaction input like "hi" still falls through to the LLM call today. This story doesn't add a short-message heuristic (see Rejected below); it removes the *serial* cost instead.
- **Why not a blind fire-and-forget:** the classify result is not pure side-channel telemetry. On a confident format reaction, the step sets `feedback_handled=True` and `_short_circuit` (`feedback.py:429-439`) *replaces the whole turn* with a confirmation; `execute` reads that flag to skip the tool loop entirely. A naive `create_task()`-and-ignore would let `execute` generate a wrong/unrelated answer to something like "no, drop the asterisks" while the real verdict races in the background — breaking the reaction UX. The verdict must still be known before the first user-visible chunk streams; only the *waiting* moves later (concurrent with useful work) instead of *before* any useful work starts.
- **Rejected alternatives:**
  - **Cheap structural pre-filter** (skip the LLM call entirely when the prior render has no enforceable artifact — no emphasis/table/bare-link and no `output_style` set) — legitimate and cheap, but trades away the positive "keep this" pin and best-effort tone notes on an already-clean render. Valid as a *future, additive* optimization layered on top of this story, not a substitute for it — this story's concurrency fix helps every turn, the pre-filter only helps the subset it can safely skip.
  - **Batching classification via the scheduler** (run it as a background/periodic job instead of inline) — rejected: breaks the inline short-circuit UX for real-time reactions (memory: reaction turns need same-turn confirmation, not a delayed background verdict).
  - **Smaller/non-LLM classifier model** — out of scope for this story (already fast-tier); worth a separate story if profiling still shows this as a bottleneck after concurrency lands.
- **Risk:** the started-but-abandoned task case (AC #5) is the main new failure mode this story introduces — an uncancelled orphan task on an error/early-exit path would leak. Must be handled explicitly, not left to Python's garbage collector (which only warns, doesn't clean up gracefully).

### Project Structure Notes

- One new `PipelineState` field (a task handle) to carry state across the `feedback` → `execute` step boundary — this is the only cross-step coupling this story introduces, and it's the standard pattern for this kind of concurrent-then-join step relationship.
- No new files, no new dependency (`asyncio.create_task` is stdlib).

### References

- [Source: src/stackowl/pipeline/registry.py#L43] — `feedback` step position immediately before `execute`
- [Source: src/stackowl/pipeline/steps/feedback.py#L100-102] — no-prior-render skip guard
- [Source: src/stackowl/pipeline/steps/feedback.py#L104-109] — `_PREFILTER_MAX_CHARS` long-message skip guard
- [Source: src/stackowl/pipeline/steps/feedback.py#L111-115] — current blocking `classify()` call site (this story's change site)
- [Source: src/stackowl/pipeline/steps/feedback.py#L429-439] — `_short_circuit`, confirmation-replaces-turn behavior that must be preserved
- [Source: src/stackowl/interaction/feedback_classifier.py#L167-239] — `FeedbackClassifier.classify`, provider resolution + LLM round-trip

## Dev Agent Record

### Agent Model Used

Claude Opus 4.8 (research); implementer TBD

### Debug Log References

### Completion Notes List

### File List
