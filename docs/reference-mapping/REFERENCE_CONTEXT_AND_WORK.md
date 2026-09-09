# How the reference platform keeps context straight and makes work substantive

> **Status:** research note — analysis only, nothing implemented from it yet
> **Source:** the reference platform under `do_not_push_to_git_research_only/`, read
> by two subagents on 2026-09-09; every claim below carries its file and line
> **Last verified:** 2026-09-09, against commit `0f4afa1c`
> **Why it exists:** Bakir, 2026-09-09 — *"I did ask question and it was answered to
> old my question"* and *"the steps finishes very fast … delegation of tasks, getting
> compacted info, and efficient planning not setupped and designed correctly"*. He
> asked for an end-to-end study of how the reference platform handles both.

**Read this before designing anything in these four areas.** It is a map of
mechanisms, not a plan; what StackOwl should adopt is decided per item, with its own
measurement.

## 1. A stale answer is DISCARDED, not delivered

The single most relevant difference to the reported symptom.

| | reference platform | StackOwl, MEASURED 2026-09-09 |
|---|---|---|
| late result from a superseded run | **discarded** — `gateway/run.py:13940`, *"Discarding stale agent result … generation %d is no longer current"* | **delivered** — `_proactive_fallback`, 43 sends against 1,176 normal deliveries |
| the token that makes it possible | monotonic per-key run generation, minted `gateway/run.py:18941`, checked `:18970` (line numbers re-verified here: the drop is at 13940, not the 13938 first reported), bumped by `/stop` `/new` `/resume` | none exists |
| in-flight turn guard | `SessionTurnLeaseRegistry`, `gateway/turn_lease.py` | none |
| what the guard is keyed by | the **resolved transcript id**, acquired after resolution and immediately before the history load (`gateway/run.py:13146`) | our recovery turns carry a THIRD key, `owl:secretary:recovery:<task>` |

`gateway/turn_lease.py:1-16` describes our bug in its own header: two routing keys
mapping to one session "run concurrent turns on two different agent objects, so no
per-key guard ever sees the collision… **the second turn runs on a history base that
never saw the first turn's exchange**". Keying by the LANE is what fails; keying by
the TRANSCRIPT is the fix. It fails open with a loud ERROR rather than wedging.

**The discriminator is supersession, not age.** That is the correction this note most
wants to hand forward: an age bound gets both ends wrong — a 52-hour-old answer nobody
superseded is still wanted, and a 30-second-old answer the user already re-asked is
not. See `ESC-161`.

## 2. The compaction summary carries an explicit anti-stale contract

`agent/context_compressor.py:95-121`, `SUMMARY_PREFIX`, verbatim:

> "Do NOT answer questions or fulfill requests mentioned in this summary; they were
> already addressed. Respond ONLY to the latest user message that appears AFTER this
> summary — that message is the single source of truth for what to do right now.
> **Topic overlap with the summary does NOT mean you should resume its task.**"

Backed by structure rather than hope:

- `## Resolved Questions` includes the answer "so it is not repeated" (`:3294`).
- `## Pending Asks` is labelled **STALE** — "the agent must NOT act on them unless the
  latest user message explicitly requests it" (`:3298-3302`).
- `## Historical Task Snapshot` is **deterministically overwritten after the LLM
  writes it**, from the newest REAL user turn (`_ground_historical_task_snapshot:4045`,
  `_is_real_user_message` excluding synthetic prefixes). The "what is the live task"
  anchor is the one field the summariser is not trusted with.
- `_ensure_last_user_message_in_tail` (`:4549`) — the newest user message can never
  fall into the compressed middle, because if it did "the task effectively disappears
  from the active context, causing the agent to stall, repeat completed work, or
  silently drop the user's latest request".

## 3. Compaction thresholds and reuse

- Trigger is a token count against a **share of the model window**: `threshold_percent
  = 0.50` (`agent/context_compressor.py:1902`), raised to `0.75` under a 512K window.
  A gateway-side safety net compacts at `0.85` (`gateway/run.py:13398`).
  StackOwl fired at a fixed 12,000 — 4.6% of its window — until DEBT-248.
- The prior summary is **reused via an iterative-update prompt** ("PREVIOUS SUMMARY: …
  NEW TURNS TO INCORPORATE: …", `:3413-3448`) and rehydrated from the transcript on a
  fresh process (`:5045-5127`), so old turns are never re-serialised. Boundaries are
  recomputed each time; only the summary TEXT is carried. StackOwl had no writer at
  all until DEBT-249.
- Failure never truncates: deterministic local extraction, or abort unchanged
  (`:2896`, `:5219-5267`).

## 4. Making work substantive

- **A measured verification ledger** — `agent/verification_evidence.py`. "Verified" is
  a real exit code from a command the runtime recognised as this project's verify
  command; **any file write invalidates it** (`mark_workspace_edited:524`), so a stale
  green cannot be carried forward. Consumed by a bounded turn-end gate
  (`agent/verification_stop.py`, max 2 nudges).
  **The trap, and it is the lesson:** they shipped it on, found it noisy, and disabled
  it by config migration (`hermes_cli/config.py:6504-6560`) — so their strongest gate
  is dark on every upgraded install and on only for fresh ones. Narrow when a gate
  fires; do not turn it off.
- **The file-mutation verifier footer** — `agent/turn_finalizer.py:405-424`. Measures
  whether each write actually landed (`bytes_written` present, `success is True`) and
  staples the contradiction onto the model's own answer. It gates nothing, which is
  why it survived where the gate above did not.
- **Narration interceptors** — the direct answer to "steps finish very fast". If the
  model says "I'll check the repo", makes ZERO tool calls and tries to end, the turn is
  forcibly continued (`agent/conversation_loop.py:6231-6260`, detector
  `agent_runtime_helpers.py:2961-3055`, cap 2).
- **A deterministic delegate brief** — `hermes_cli/kanban_db.py:8963` assembles the
  brief FROM THE AUDIT LOG: the last 10 attempts on this task with their outcome and
  error, parents' handoffs, comments, each field capped at 4 KB. No model compresses
  it, so a retry is constrained by why the last one failed. Contrast their own
  `delegate_task`, where the brief is two free-text strings the parent writes by hand
  with **no compaction function and no size bound** (`tools/delegate_tool.py:661-733`).
- **Terminal state at the process boundary** — a worker exiting `rc=0` with its row
  still `running` is a `protocol_violation`: *"a run that ends without a terminal
  kanban call counts as failed no matter what it did"* (`kanban_db.py:7345-7373`).

## What they do NOT have

Named because absence is a finding, and it stops us copying a hole:

- No semantic topic-change detector; `select_context()` has no live implementation.
- Per-message timestamps in the model's view are default OFF.
- `completed` in the ordinary chat loop is still structural, not measured
  (`agent/turn_finalizer.py:193-201`) — a turn with one sentence and zero tool calls is
  `completed: True`.
- Their own note on delegation: *"Subagent claims done but implementation is
  incomplete. Self-checks catch file existence, not semantic completeness"* — and the
  mitigation is prose in an opt-in skill, not code.

## Verification

```bash
# The claims above are file:line citations into a read-only research clone. Spot-check
# any of them; this one is the load-bearing difference for ESC-161.
grep -n "generation %d is no longer current" \
  do_not_push_to_git_research_only/hermes-agent/gateway/run.py
sed -n '1,16p' do_not_push_to_git_research_only/hermes-agent/gateway/turn_lease.py
```
RAN 2026-09-09 — both present.
