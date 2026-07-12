# Token Usage Display — Design

Status: approved (design phase)
Feature 3 of 3 (retry loop → like/dislike → token display)

## Problem

Per-call token usage is already captured (`cost_tracker.py`'s `CostRecord`,
`cost_records` table, migration 0010) but never surfaced to the user —
tracked for cost/budget enforcement only.

## Goal

Every final Telegram answer shows total tokens sent/received for that turn,
appended to the answer text, immediately before the like/dislike buttons
(feature 2).

## Non-goals

- Not adding new token-capture logic — `cost_tracker.py` already captures
  everything needed.
- Not gating display on answer length (unlike feature 2's rating buttons) —
  token info is cheap and always relevant.
- Not showing per-call breakdown (classifier vs. answer-generation) — one
  summed total for the turn.

## Data source

`cost_records` table (migration 0010), existing columns include
`trace_id`, `input_tokens`, `output_tokens` (per `CostRecord` dataclass,
`cost_tracker.py:24-33`). One new read query:

```sql
SELECT SUM(input_tokens) AS total_input, SUM(output_tokens) AS total_output
FROM cost_records WHERE trace_id = ?
```

Sums across every LLM call recorded for that turn (classifiers + main
answer generation) — matches what was actually spent that turn, per
decision.

## Placement and timing

Appended as a line to the final answer text at the same point feature 2
attaches its keyboard (`consolidate.py`, after `execute` has finished — all
of that turn's `cost_records` rows are already written by then, since
`cost_tracker` writes synchronously per call). Format:
`\n\n🔢 {total_input:,} in / {total_output:,} out`.

Ordering in the final message: answer text → token line → like/dislike
buttons (buttons are a keyboard, not text, so ordering is: text block
[answer + token line] with the keyboard attached below it).

## Error handling

If the query returns no rows for that `trace_id` (a turn with zero tracked
LLM calls — e.g. pure cache hit or non-LLM slash command), append nothing.
Silently wrong data (`0 in / 0 out`) is worse than no line at all.

## Testing

`tests/pipeline/test_token_usage_display.py`:
- a turn with multiple `cost_records` rows (classifier + answer call) gets
  the SUMMED total appended, not just the last call's numbers.
- a turn with zero `cost_records` rows gets no token line appended (answer
  text unchanged).
- the token line appears before any keyboard attachment, on the same final
  chunk feature 2's keyboard attaches to.
