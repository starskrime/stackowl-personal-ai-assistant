# Schedule-Parse Crash + owl_build Misfire — Fix Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix two independently root-caused live bugs: (1) a malformed `daily@HH:MM` schedule string crashes `reconcile_owl_schedules` on every boot with no validation gate to catch it earlier, and (2) the `secretary` owl calls `owl_build` in response to plain greetings ("Hi") because a prior incident's fix made `owl_build` a permanent tool on every turn, which interacts with unconditional short-term history replay to keep re-surfacing an old unresolved "create Brain" exchange.

**Architecture:** Bug 1 gets a real format validator at manifest-load time (reject bad schedules before they're ever persisted) plus a defensive fail-open parse at the actual crash site (belt + suspenders, matching the existing pattern used by the cron branch two lines below it). Bug 2 gets the surgical fix the evidence supports — the turn's own delivered "capability failed: owl_build" floor text is stale/wrong (the LAST owl_build call in that same turn actually succeeded via `edit`), and that stale failure text is what's priming the model to keep re-attempting the "task" on every subsequent unrelated turn. We do NOT remove `owl_build` from the guaranteed-tool set (that would regress the original incident it was added to fix) and we do NOT touch history-replay window size (that's normal, correct behavior elsewhere) — the user has explicitly confirmed the platform's persistent, never-give-up behavior is desired; the ask is for it to be *smart*, not less persistent.

**Tech Stack:** Python 3.14, pydantic, pytest.

## Global Constraints

- Minimal code changes — touch only the exact lines needed (per this repo's `feedback_minimal_code_changes` convention).
- Every `except`/error path must log (4-point logging standard already used throughout this codebase).
- No new dependencies.
- Every non-trivial change gets a test.

---

### Task 1: Reject malformed `daily@HH:MM` schedules at manifest-validation time

**Files:**
- Modify: `src/stackowl/owls/owl_schedule_guards.py:41-52` (`schedule_interval_seconds`)
- Test: `tests/owls/test_owl_schedule_guards.py` (create if it doesn't already cover this — check first with `ls tests/owls/ | grep schedule_guard`)

**Interfaces:**
- Consumes: nothing new — reuses the existing `daily@HH:MM` regex logic already written (and working) in `src/stackowl/tools/scheduling/cron_helpers.py:143-151`'s `is_valid_schedule`.
- Produces: `schedule_interval_seconds("daily@09:30 CDT")` now returns `None` instead of `86400.0`, which makes `interval_floor_error` (the same file, line 80) skip the floor check (as documented: "An unparseable interval is NOT rejected here") — the manifest's OWN loader is what actually rejects it (see below).

Today, `owl_schedule_guards.py:41-52`:
```python
def schedule_interval_seconds(schedule: str) -> float | None:
    text = schedule.strip()
    if text.lower().startswith("daily@"):
        return 86400.0
    every = parse_every(text)
    ...
```
Any string merely *starting with* `"daily@"` is accepted — the `HH:MM` body is never checked. This is why `"daily@09:30 CDT"` sailed through manifest construction untouched and only blew up later, deep in `compute_next_run`.

- [ ] **Step 1: Write the failing test**

Add to `tests/owls/test_owl_schedule_guards.py` (create the file if it doesn't exist — check `tests/owls/` first):

```python
from stackowl.owls.owl_schedule_guards import schedule_interval_seconds


def test_schedule_interval_seconds_rejects_malformed_daily_body():
    # A stray suffix (e.g. an accidentally-typed timezone abbreviation) after
    # the HH:MM body must not be silently accepted as a valid daily schedule.
    assert schedule_interval_seconds("daily@09:30 CDT") is None


def test_schedule_interval_seconds_accepts_clean_daily():
    assert schedule_interval_seconds("daily@09:00") == 86400.0


def test_schedule_interval_seconds_rejects_out_of_range_hour():
    assert schedule_interval_seconds("daily@24:00") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/owls/test_owl_schedule_guards.py -v`
Expected: `test_schedule_interval_seconds_rejects_malformed_daily_body` FAILS (currently returns `86400.0`, not `None`). `test_schedule_interval_seconds_rejects_out_of_range_hour` also FAILS for the same reason.

- [ ] **Step 3: Write minimal implementation**

Edit `src/stackowl/owls/owl_schedule_guards.py`. First check the existing imports at the top of the file (lines 1-20) — add an import for the shared body-parser. Since `cron_helpers.py`'s `is_valid_schedule` doesn't expose the `daily@` body-parsing as a standalone reusable function, add one there and import it, rather than duplicating the parse logic a third time:

In `src/stackowl/tools/scheduling/cron_helpers.py`, replace the inline `daily@` block inside `is_valid_schedule` (the block starting `if lowered.startswith("daily@"):` around line 143) with a call to a new small helper, and add that helper right above `is_valid_schedule`:

```python
def parse_daily_hhmm(schedule: str) -> tuple[int, int] | None:
    """Parse a strict ``daily@HH:MM`` body, or None if malformed/out-of-range.

    Single source of truth for the ``daily@`` format — reused by
    ``is_valid_schedule`` (this file) and by
    ``owl_schedule_guards.schedule_interval_seconds`` so a malformed schedule
    (e.g. a stray suffix like "daily@09:30 CDT") is rejected identically at
    every gate instead of silently accepted by one and crashing another.
    """
    text = schedule.strip()
    if not text.lower().startswith("daily@"):
        return None
    body = text[len("daily@"):]
    parts = body.split(":")
    if len(parts) > 2:
        return None
    try:
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
    except (ValueError, IndexError):
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute
```

Then update `is_valid_schedule`'s `daily@` branch (currently lines 143-151) to:
```python
    if lowered.startswith("daily@"):
        return parse_daily_hhmm(text) is not None
```

Now in `src/stackowl/owls/owl_schedule_guards.py`, add the import and fix `schedule_interval_seconds`:
```python
from stackowl.tools.scheduling.cron_helpers import parse_daily_hhmm
```
(add alongside the file's existing imports, matching whatever import style the rest of the file already uses — check lines 15-25 first)

```python
def schedule_interval_seconds(schedule: str) -> float | None:
    text = schedule.strip()
    if text.lower().startswith("daily@"):
        return 86400.0 if parse_daily_hhmm(text) is not None else None
    every = parse_every(text)
    ...
```
(keep the rest of the function body unchanged)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/owls/test_owl_schedule_guards.py -v`
Expected: all 3 PASS.

Also run the existing cron_helpers test suite to confirm the refactor didn't regress the working validator:
Run: `uv run pytest tests/tools/scheduling/ -k "schedule" -v` (check the exact test file name first with `ls tests/tools/scheduling/ | grep -i cron`)
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/stackowl/owls/owl_schedule_guards.py src/stackowl/tools/scheduling/cron_helpers.py tests/owls/test_owl_schedule_guards.py
git commit -m "fix(scheduler): reject malformed daily@HH:MM schedule bodies at validation time

schedule_interval_seconds() accepted any string merely starting with
'daily@' without checking the HH:MM body, letting a malformed schedule
(e.g. a stray 'daily@09:30 CDT' typo) sail through manifest validation
and crash reconcile_owl_schedules later at the actual parse site.
Extracts the strict HH:MM parser (already correct in
cron_helpers.is_valid_schedule) into parse_daily_hhmm() and reuses it
in both places so a malformed schedule is rejected identically
everywhere instead of accepted by one gate and crashing another."
```

---

### Task 2: Fail open (not raise) on a malformed `daily@` schedule inside `compute_next_run`

**Files:**
- Modify: `src/stackowl/scheduler/scheduler_helpers.py:186-190`
- Test: `tests/scheduler/test_scheduler_helpers.py` (check exact filename first: `ls tests/scheduler/ | grep -i helper`)

**Interfaces:**
- Consumes: `parse_daily_hhmm` from Task 1 (`src/stackowl/tools/scheduling/cron_helpers.py`).
- Produces: `compute_next_run("daily@09:30 CDT", tz="America/Chicago")` no longer raises `ValueError` — falls through to the existing cron-parse attempt, and if that also fails, hits the same `+1d` fallback the generic cron branch already uses (lines ~211-223), with a logged warning.

This is defense-in-depth: Task 1 stops a NEW malformed schedule from ever being saved, but an already-corrupted DB row (or a manifest loaded before Task 1 shipped) must not crash `reconcile_owl_schedules` — it should degrade the same way every other unparseable schedule already does in this function (log + fall back), not raise a raw `ValueError` out of a function three other call sites depend on.

- [ ] **Step 1: Write the failing test**

Add to `tests/scheduler/test_scheduler_helpers.py`:
```python
from stackowl.scheduler.scheduler_helpers import compute_next_run


def test_compute_next_run_fails_open_on_malformed_daily_body():
    # Must not raise — a corrupted schedule (already-saved before Task 1's
    # validator existed) should degrade gracefully like every other
    # unparseable schedule this function already handles.
    result = compute_next_run("daily@09:30 CDT", tz="UTC")
    assert result is not None  # some ISO-8601 fallback timestamp, not a raised exception
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/scheduler/test_scheduler_helpers.py::test_compute_next_run_fails_open_on_malformed_daily_body -v`
Expected: FAIL with `ValueError: invalid literal for int() with base 10: '30 CDT'`.

- [ ] **Step 3: Write minimal implementation**

Edit `src/stackowl/scheduler/scheduler_helpers.py`. Add the import near the top (alongside the file's other `stackowl` imports):
```python
from stackowl.tools.scheduling.cron_helpers import parse_daily_hhmm
```

Replace lines 186-190:
```python
    if schedule.startswith("daily@"):
        parts = schedule[len("daily@") :].split(":")
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
        return _next_local_hhmm(hour, minute, tz=tz, now=now)
```
with:
```python
    if schedule.startswith("daily@"):
        parsed = parse_daily_hhmm(schedule)
        if parsed is not None:
            hour, minute = parsed
            return _next_local_hhmm(hour, minute, tz=tz, now=now)
        log.scheduler.warning(
            "[scheduler] compute_next_run: malformed daily@ body — falling through",
            extra={"_fields": {"schedule": schedule}},
        )
        # Falls through to the cron/other-form attempts below; if none match,
        # the existing try/except cron branch's own fallback applies.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/scheduler/test_scheduler_helpers.py -v`
Expected: all PASS, including the new test and every pre-existing test in this file (no regressions to the clean `daily@HH:MM` path — re-run Task 1's `schedule_interval_seconds` tests too since both now share `parse_daily_hhmm`).

- [ ] **Step 5: Commit**

```bash
git add src/stackowl/scheduler/scheduler_helpers.py tests/scheduler/test_scheduler_helpers.py
git commit -m "fix(scheduler): fail open instead of raising on a malformed daily@ schedule

compute_next_run's daily@ branch had no format validation and raised a
raw ValueError straight out of the function on a malformed body (e.g.
a stray timezone-abbreviation suffix), crashing
reconcile_owl_schedules's caller on every boot for the affected owl.
Now reuses parse_daily_hhmm (shared with cron_helpers.is_valid_schedule
and owl_schedule_guards.schedule_interval_seconds) and falls through to
the existing cron-parse/+1d fallback path on a malformed body, matching
this function's established fail-open pattern for every other
unparseable schedule shape."
```

---

### Task 3: Stop a stale "capability failed" floor message from priming the next turn's owl_build misfire

**Context (do not skip — this is the load-bearing finding):** In the live incident trace, `secretary`'s tool_loop called `owl_build(action=create, name=Brain)` twice (both failed: "already exists"), then `owl_build(action=edit, name=Brain)` — which **succeeded**. But the turn's floor/give-up gate (`overclaim.detected` in the log) delivered the user a message saying *"I couldn't fully complete this: Hi. The capability that failed: owl_build."* — describing the EARLIER failed attempts, not the LATER successful one. That stale "owl_build failed" text then sits in the next turn's short-term history (`classify.py`'s `_gather_history`, unconditionally replayed — this part is correct, working-as-designed behavior and is NOT being changed), and the charter's "take full ownership, drive every request to a delivered outcome" framing (`owls/base_prompt.py`) makes the model treat the (already-actually-resolved) task as still open, re-triggering `owl_build` on the next unrelated "Hi". The user has explicitly confirmed they WANT this persistent, never-give-up behavior — the fix here is to make the SIGNAL the model acts on accurate (it already succeeded), not to make the model less persistent.

**Files:**
- Read first (to find the exact synthesis site — this file/line was not pinned down by the research pass, so Step 1 below is a locate-then-fix step): `src/stackowl/pipeline/supervisor.py` (`synthesize_from_calls` — referenced from `providers/anthropic_provider.py`/`openai_provider.py` as the give-up-floor text builder) and `src/stackowl/pipeline/persistence.py` (`summarize_tool_outcomes`, `TOOL_FAILED_MARKER` usage).
- Test: `tests/pipeline/test_supervisor_floor_summary.py` or wherever the existing tests for `synthesize_from_calls`/`summarize_tool_outcomes` live — locate with `grep -rl "synthesize_from_calls\|summarize_tool_outcomes" tests/`.

**Interfaces:**
- Consumes: `all_calls: list[dict]` — the turn's full tool-call record list (each entry presumably has at least a tool name + success/failure), already threaded through `complete_with_tools`'s `all_calls` accumulator (see `providers/anthropic_provider.py`, `_round`'s tool-call handling) into `synthesize_from_calls(user_text, all_calls, text)`.
- Produces: the floor summary text must describe the LAST outcome for a given tool name/target when the SAME capability was retried and eventually succeeded — not just the first failure.

- [ ] **Step 1: Locate the exact synthesis code**

Run: `grep -n "def synthesize_from_calls\|def summarize_tool_outcomes" -A 40 src/stackowl/pipeline/supervisor.py src/stackowl/pipeline/persistence.py`

Read the full matched function bodies. You are looking for the loop/logic that turns `all_calls` (a list of per-tool-call records accumulated across the WHOLE turn, including retries) into the user-facing "the capability that failed: X" sentence. Confirm: does it iterate `all_calls` and pick the FIRST failure it finds, or does it not de-duplicate by tool name at all (reporting every distinct call, including ones later superseded by a success)?

- [ ] **Step 2: Write the failing test**

Once you've confirmed the exact function and its current signature/behavior from Step 1, write a test that constructs an `all_calls`-shaped list matching the real incident shape: two failed `owl_build` calls followed by one successful `owl_build` call (same tool name, same target `name="Brain"`, different `action`s: `create`, `create`, `edit`), and asserts the synthesized floor text does NOT claim `owl_build` as a failed capability when the LAST call for that tool succeeded. Model the exact test after whatever existing tests for this function already look like (match their fixture/call shape exactly — do not invent a different record shape than what `all_calls` actually is; confirm the real shape from `providers/anthropic_provider.py`'s `all_calls.append(...)` call sites before writing the test).

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest <path to new test> -v`
Expected: FAIL — current logic reports the failure even though a later same-tool call succeeded (confirm this is the actual failure mode from Step 1's read before writing the fix).

- [ ] **Step 4: Write minimal implementation**

Based on what Step 1 found: the fix is to key the "did this capability ultimately fail" logic on the LAST outcome per (tool name, target-identifying-arg) pair within `all_calls`, not the first, or not an unconditional "any failure present" check. Do not guess the exact diff here — write it against the real function body found in Step 1. The shape should be: group `all_calls` by an identity key (tool name at minimum; include a target-like arg such as `name` if the record carries one), keep only the LAST record per key, and report failure only for keys whose last record failed.

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest <path to new test> -v`
Expected: PASS. Also run the full existing test suite for this function/module to confirm no regression:
Run: `uv run pytest tests/pipeline/ -k "supervisor or floor or synthesize" -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add <files touched in Step 4> <new test file>
git commit -m "fix(pipeline): floor summary reports the LAST outcome per capability, not the first

A turn that retried the same tool (e.g. owl_build create -> create ->
edit, with only the edit succeeding) synthesized a floor message
claiming the capability failed, describing an EARLIER attempt instead
of the eventual success. That stale failure text then persisted into
conversation history and primed the next unrelated turn's model to
keep re-attempting the (already-resolved) task -- the mechanism behind
a live 'Hi' -> owl_build misfire incident. Synthesis now keys on the
last recorded outcome per (tool, target) pair."
```

---

### Task 4 (optional, low-risk, do only if Task 3 alone doesn't fully stop the misfire in a live retest): Tighten `owl_build`'s tool description

**Files:**
- Modify: `src/stackowl/tools/meta/owl_build.py:115-127` (`OwlBuildTool.description`)

**Interfaces:** none — pure docstring/description text, no signature change.

Current description already has a "RARE" caveat but nothing telling the model to ignore unrelated prior-turn context when the CURRENT message doesn't ask for agent creation/editing. This is a prompt-quality nudge, not a root-cause fix — only do this if Task 3's fix (verified live) doesn't fully stop the recurrence, since a description change alone cannot be unit-tested for effectiveness and is easy to over-tune.

- [ ] **Step 1: Add one sentence to the description**

In `src/stackowl/tools/meta/owl_build.py`, in `OwlBuildTool.description`, append after the existing "RARE:" sentence:
```
Only call this when the user's CURRENT message explicitly asks to
create/edit/retire a named agent — never as a follow-up to an earlier
turn's unrelated request, even if an earlier attempt failed.
```

- [ ] **Step 2: Manually smoke-test**

There is no automated way to verify an LLM's tool-selection behavior changed from a description edit alone. After deploying (config hot-reloads; tool descriptions are read fresh per turn — confirm this by checking whether `to_provider_schema()`/tool_schemas are rebuilt per-request or cached at startup, per `src/stackowl/tools/registry.py`), send a plain "Hi" in a session whose history contains a resolved owl_build exchange, and confirm `owl_build` is not called.

- [ ] **Step 3: Commit**

```bash
git add src/stackowl/tools/meta/owl_build.py
git commit -m "fix(tools): tighten owl_build description to require an explicit current-turn request

Nudges the model away from re-attempting an already-resolved
create/edit from a prior turn's history just because owl_build is
always present in the tool list. Complements the Task 3 floor-summary
fix; not a substitute for it."
```

---

## Self-Review Notes

- **Spec coverage:** Task 1+2 fully cover the schedule-parse crash (validation gate + defensive fail-open). Task 3 covers the confirmed causal mechanism for the owl_build misfire (stale floor summary → history → re-trigger). Task 4 is an explicitly-optional prompt nudge, not depended on by anything.
- **Known gap, not covered by this plan:** removing `owl_build` from `_infra/presentation.py`'s `_DEFAULT_BASE` was considered and explicitly REJECTED — that tool's permanent presence is itself the fix for a prior real incident ("create an agent named Brain" — see the comment at `presentation.py:77-78`). Do not revert it as part of this work. The user has confirmed persistent/never-give-up tool availability is desired; this plan makes the platform *smarter* about it, not less persistent.
- **Type/signature consistency:** `parse_daily_hhmm(schedule: str) -> tuple[int, int] | None` is defined once in Task 1 and consumed identically by Task 2 — same name, same signature, both tasks reference it.
