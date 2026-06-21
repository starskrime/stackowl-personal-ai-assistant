---
name: write-your-own-skill
description: Use after completing a non-trivial multi-tool task worth reusing. Calls the synthesize_skills tool (consent-gated) to codify the successful sequence into a reusable learned skill.
when_to_use: When a multi-step task succeeded and the procedure is general enough to be worth capturing — so the same sequence can be recalled and applied to similar future requests without rediscovering it.
version: 0.1.0
tags: [skill-authoring, learning, synthesis]
author: stackowl-builtin
license: MIT
---

# Write Your Own Skill

When a multi-tool sequence succeeds, capturing it as a reusable skill means the
same procedure is available in future sessions without rediscovering it. This
skill guides that capture step: what to include, how to call the synthesis tool,
and how to confirm the skill was actually persisted.

## Steps

1. **Decide whether the sequence is worth capturing.** A sequence is worth a
   skill if it: (a) worked end-to-end, (b) is general enough to apply to at
   least one other plausible future request, and (c) is non-trivial — more than
   two steps or involves tool choices that are not obvious. Skip sequences that
   failed, that were highly one-off, or that are already covered by an existing
   skill (`skills_list` to check).

2. **Draft the skill content before calling the tool.** The synthesis call
   should include at minimum: a clear procedure (the ordered steps that worked),
   the pitfalls encountered or anticipated, and a verification step. Do not
   capture a bare "I did X then Y" log — write it as a reusable playbook.

3. **Call `synthesize_skills` with the drafted content.** This tool is
   consent-gated: it will ask the user to confirm before persisting. Pass the
   procedure, pitfalls, and verification steps as structured content so the
   stored skill is immediately usable, not just a memory of what happened.

4. **Confirm the skill was persisted.** After `synthesize_skills` returns,
   call `skills_list` to verify the new skill name appears. Do not claim the
   skill was saved if the tool declined, was cancelled by the user, or returned
   an error.

## Verification

Before claiming the skill was written, confirm:

- `synthesize_skills` returned a success result (not a consent refusal or
  an error).
- The new skill appears in the output of `skills_list`.
- The stored content includes procedure steps, at least one pitfall, and a
  verification check — not just a description of the outcome.

## Pitfalls

- **Capturing a failed or uncertain sequence.** Only synthesise a skill from
  a sequence that demonstrably worked. A skill built from a partially-successful
  or uncertain execution will mislead future uses.
- **Omitting verification steps from the new skill.** A skill without a
  verification section teaches the model to skip evidence-gathering on future
  uses. Always include "how to confirm this worked" in what you capture.
- **Claiming a skill was saved without confirming.** `synthesize_skills` is
  consent-gated and may be declined. Check `skills_list` after the call; do not
  assume the tool's invocation equals persistence.
- **Duplicating an existing skill.** Check `skills_list` before synthesising.
  If a skill already covers the procedure, prefer updating it (note the
  successful run) rather than creating a near-duplicate.
