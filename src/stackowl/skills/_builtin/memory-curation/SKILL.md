---
name: memory-curation
description: Use to deliberately store, consolidate, and verify durable facts and preferences — either after a session rich with keeper information or on a periodic curation pass.
when_to_use: After a session that surfaced facts or preferences worth keeping long-term, or when the user explicitly asks to remember something. Also use on a periodic curation pass to promote staged facts and remove stale or duplicate entries.
version: 0.1.0
tags: [memory, curation, preferences, knowledge, facts]
author: stackowl-builtin
license: MIT
---

# Memory Curation

Conversation-only context evaporates when the session ends. This skill steers
deliberate, verified storage of durable facts and preferences so that future
sessions benefit from what was learned. The background consolidation worker also
runs automatically, but this skill drives intentional curation when the user or
the assistant identifies something worth preserving right now.

## Steps

1. **Identify what is worth storing.** Separate durable facts (preferences,
   decisions, recurring patterns, stable context about the user or their
   environment) from transient conversation detail (one-off intermediate values,
   session-specific scratchpad content). Only proceed with the durable items.

2. **Store each fact or preference with the `memory` tool.** Call `memory` once
   per distinct item, using clear, self-contained phrasing that will make sense
   in a future session with no surrounding context. Tag items appropriately
   (e.g. preference, fact, decision) so they surface in relevant searches.

3. **Trigger deliberate consolidation with `reflect_now`.** This signals the
   consolidation layer to process staged entries immediately rather than waiting
   for the next background cycle. Use this when the user wants confirmation that
   something is retained before the session ends.

4. **Recall to confirm retention.** After storing, call the `memory` tool in
   recall mode (or use a recall tool) to retrieve the just-stored item and
   confirm it appears in the result. Do not tell the user something was
   remembered until this step confirms it.

## Verification

Before claiming anything was remembered:

- The recall step in Step 4 must return the stored item (or an item equivalent
  in meaning). If the store declined or the recall returns nothing, do not
  claim success — report the failure honestly and suggest the user try again or
  check storage limits.
- Do not conflate a successful `memory` call with confirmed persistence; only
  a successful recall proves the item survived consolidation.
- If `reflect_now` reports an error, note it; the background worker may still
  consolidate later, but that is uncertain and should be communicated as such.

## Pitfalls

- **Storing transient detail as durable.** One-off values, intermediate
  calculations, or conversation-specific context pollute the memory store and
  reduce retrieval quality. Only store things that will be useful in a future
  session.
- **Storing secrets or credentials.** Never pass passwords, tokens, API keys,
  or other sensitive values to the `memory` tool. The store is persistent and
  may be read in contexts where those values would be exposed.
- **Duplicate facts.** Before storing, consider whether an equivalent fact
  already exists. Storing near-duplicate entries degrades search quality and
  wastes storage. If in doubt, recall first and update rather than add.
- **Claiming success before recall confirms it.** A `memory` call that returns
  no error is not proof of persistence. Always confirm with a recall step before
  telling the user the item is remembered.
- **Over-curating a single session.** Bulk-storing every detail from a long
  conversation creates noise. Be selective: prefer a few high-signal durable
  items over many low-signal ones.
