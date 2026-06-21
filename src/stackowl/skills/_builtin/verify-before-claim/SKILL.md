---
name: verify-before-claim
description: Use before telling the user a task is done, a file was written, or a fix works. Re-reads the artifact and re-runs the check so the reply is grounded in a fresh observation, not intent.
when_to_use: Before asserting that a file was written, a command succeeded, a test passes, or any other task is complete — whenever the reply would claim a result rather than describe an attempt.
version: 0.1.0
tags: [verification, honesty, quality]
author: stackowl-builtin
license: MIT
---

# Verify Before Claiming Success

Claiming success based on intent rather than evidence is a common failure mode.
This skill enforces a concrete evidence-gathering step before the final reply so
the user always receives a grounded, honest report — not a confident guess.

## Steps

1. **Re-read the artifact.** Call `read_file` on any file that was supposed to
   be created or modified. Confirm the expected content is actually present.
   Do not rely on the return value of a write call alone.

2. **Re-run the check.** Call `shell` or `execute_code` to run the test, lint,
   build, or validation command relevant to the task. Read the exit code and
   output — do not assume the previous run's result is still valid.

3. **Compare expected vs. actual.** Match the observed output against what the
   user asked for. Note any discrepancy explicitly.

4. **Cite the concrete evidence in the reply.** Quote the relevant line from the
   file, paste the test pass/fail summary, or state the exact command and its
   exit code. Never summarise without the supporting observation.

## Verification

Before marking this skill's own execution complete, confirm:

- A `read_file` or `shell` / `execute_code` call was made AFTER the last write
  or action — not before it, and not skipped.
- The cited evidence matches what the user requested; if it does not, say so
  and describe what was found instead.
- If the check fails, report the failure honestly and stop — do not soften the
  result or claim partial success for a consequential failure.

## Pitfalls

- **Claiming success from intent.** Writing a file and immediately saying
  "Done — the file has been updated" without reading it back is the canonical
  mistake this skill prevents.
- **Stale reads.** If `read_file` was called before the write, it does not
  count. The verification read must come after the mutation.
- **Swallowed non-zero exit codes.** A `shell` call that returned a non-zero
  exit code but produced some output can look like success. Always check the
  exit code, not just the presence of output.
- **Scope creep.** Verification should be scoped to what the user asked for.
  Running a full test suite when only one file was touched is unnecessary and
  can produce confusing failures unrelated to the task.
