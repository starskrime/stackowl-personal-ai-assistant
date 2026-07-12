# Like/Dislike Approach Rating Buttons — Design

Status: approved (design phase)
Feature 2 of 3 (retry loop → like/dislike → token display)

## Problem

The only feedback signal today is free-text, LLM-classified by
`FeedbackClassifier`, reacting to whatever the user happens to type next.
There's no explicit, low-friction way for the user to rate *how* a turn was
handled (tool choice, reasoning, setup) — distinct from rating the output
content itself, which the existing free-text path already covers reasonably
well.

## Goal

Every substantial final answer in Telegram gets a Like/Dislike button row.
Tapping either rates the *approach* used that turn (not the output content).
Likes feed the existing DNA-evolution pipeline like any other positive
signal. Dislikes are recorded for visibility but never mutate DNA — per the
existing positive-only-learning principle (deliberate: negative-signal DNA
mutation caused instability previously).

## Non-goals

- Not replacing or changing the free-text `FeedbackClassifier` path — this
  is an additional, explicit signal, not a redesign of the existing one.
- Not adding buttons to progress/ephemeral/floor messages.
- Not building a new persistence store — extends `task_outcomes`.

## Scope: which messages get buttons

A message qualifies when:
- It is a real final answer chunk (`kind == "answer"`, `is_floor == False`).
- Combined answer text length ≥ 200 chars (a length floor, no LLM
  classification needed — pure structural check).

## Signal model

New `FeedbackSignal` aspect value: `"approach"` (existing aspects:
`content`, `format`, `length`, `tone`, `overall` — this is a fifth,
button-only aspect; free text is never classified into it since the button
is the unambiguous source for this specific signal).

`task_outcomes` gets one new nullable column: `approach_rating TEXT CHECK
(approach_rating IN ('positive', 'negative') OR approach_rating IS NULL)`.
Written via a new `TaskOutcomeStore.set_approach_rating(trace_id, rating)`
**UPDATE** (not insert — the row for that `trace_id` already exists,
written synchronously at end-of-turn by the existing `record()` call, since
`record()` uses `ON CONFLICT(trace_id) DO NOTHING` — a second insert would
silently no-op).

- `positive` → **must be wired into DNA evolution's actual query** —
  unverified at design time whether evolution's existing positive-signal
  read (`evolution_limits.py`/`dna_attribution.py`) is keyed off
  `task_outcomes.success` (task completed without error) or off an
  explicit feedback signal. The implementation task's first step is to
  read that query and confirm/extend it to also treat
  `approach_rating='positive'` as contributing — do not assume it already
  does. This is a real open question, not a solved reuse.
- `negative` → recorded, deliberately excluded from whatever that same
  query becomes (the positive-only principle applies at read time, in that
  one query — no separate write-suppression needed since the column simply
  isn't selected for on the negative branch).

## UI mechanics — reusing existing infrastructure

No new UI primitive. Mirrors `consent.py`'s established pattern exactly:
- `InlineKeyboardBuilder` (`keyboard.py`) builds the 👍/👎 row.
- New `CallbackRouter` prefix `apr:` (approach rating), payload carries
  `trace_id` + `vote` — same short-id-in-callback-data pattern as
  `command_buttons.py`.
- On click: `_edit_to_decision`-style edit — buttons removed, message text
  gets a one-line suffix (`👍 Liked` / `👎 Disliked`).
- Re-tapping the other button changes the vote (not additive) — the edit
  handler re-checks the row's current `approach_rating` and overwrites.
- One vote recorded per message; the in-place edit is itself the "already
  voted" UI state (no buttons left to tap a second time on the same
  message once one has landed, until a change-vote tap on the *other*
  button — which the callback still accepts since the message can still be
  edited).

## Data flow

1. Turn completes, `persist_turn`/existing delivery path sends the answer.
2. Delivery step (same message-send call site) checks the qualification
   rule above; if it qualifies, attaches the Like/Dislike keyboard to the
   send call (existing `send_inline_keyboard`/actions branch, not the
   plain-text branch — this reuses the message_id capture that branch
   already has, per Feature 1's research: the actions branch is the one
   branch that already keeps the sent `Message` object).
3. User taps a button → `CallbackRouter` dispatches to the new handler →
   `TaskOutcomeStore.set_approach_rating(trace_id, vote)` → edit message.

## Error handling

- Missing `task_outcomes` row for the trace (turn somehow didn't record
  one) → log and no-op the edit gracefully (matches `consent.py`'s
  `message_id is None` guard pattern) — never raise into the callback
  handler.
- Double-tap race (two rapid taps) → last-write-wins on the UPDATE; no
  locking needed, consistent with how `consent.py` handles its own
  resolve-once flow (idempotent by design — an UPDATE is naturally
  idempotent per value).

## Testing

`tests/channels/telegram/test_approach_rating_buttons.py`:
- a qualifying final answer (≥200 chars, not floor) gets the keyboard
  attached on send.
- a non-qualifying answer (short, or `is_floor=True`) does not.
- tapping Like calls `set_approach_rating(trace_id, "positive")` and edits
  the message to remove buttons.
- tapping Dislike calls `set_approach_rating(trace_id, "negative")`.
- `set_approach_rating` on a missing `task_outcomes` row logs and returns
  without raising.
- DNA-evolution's existing outcome query, given a mixed set of positive and
  negative `approach_rating` rows, only surfaces the positive ones (proves
  the positive-only principle holds through this new column).
