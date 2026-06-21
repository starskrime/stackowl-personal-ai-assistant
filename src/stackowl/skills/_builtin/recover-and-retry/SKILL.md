---
name: recover-and-retry
description: Use when a tool call fails or returns an error mid-task. Guides the structured recovery sequence — read the error, try an alternative, retry once if transient, then report honestly if still failing.
when_to_use: When a tool call returns an error, a shell command exits non-zero, or a fetch fails, and the task cannot be completed without recovering from that failure.
version: 0.1.0
tags: [recovery, error-handling, resilience]
author: stackowl-builtin
license: MIT
---

# Recover and Retry After Tool Failures

A single tool failure mid-task does not have to abort the whole goal — but
recovering blindly or hiding the failure behind a cheerful message is worse than
stopping. This skill provides the structured sequence: understand the error,
attempt a safe alternative, retry once if the failure looked transient, and
report honestly if none of that worked.

Note: the platform also performs automatic substitution and retries on certain
failure classes. This skill steers the model to cooperate with that mechanism,
not fight it by retrying the same tool indefinitely.

## Steps

1. **Read the error carefully.** Do not retry immediately. Identify whether the
   failure is transient (timeout, rate-limit, temporary network issue) or
   structural (wrong path, missing permission, bad input). The recovery path
   differs entirely.

2. **Try an in-bounds non-consequential alternative.** If a `web_fetch` times
   out, try a different URL or a `web_search` for the same information. If
   `shell` fails on a command, try `execute_code` for a pure-computation
   alternative if one exists. The alternative must be non-consequential (read or
   compute only) — never substitute a write for a failed write without explicit
   user consent.

3. **Retry the original once if the failure looked transient.** Transient signals
   include timeout messages, 429/503 HTTP codes, and "connection reset" errors.
   Retry at most once. If it fails again, treat it as structural.

4. **Stop and report honestly if still failing.** Do not loop. State what tool
   was called, what error was returned, what alternative was tried, and what the
   user should do next (e.g. check permissions, try again later, provide a
   different input). Never paper over a consequential failure with a vague
   "I ran into a small issue" summary.

## Verification

Before reporting recovery as successful, confirm:

- The alternative or retry actually produced the needed result — not just a
  non-error response. An empty result that looks like success is still a
  failure.
- No consequential action (write, delete, send) was retried more than once
  without the user being informed, since each attempt may have had a side
  effect.
- If the recovery failed, the report names the original tool, the error, the
  alternative tried, and the outcome of the alternative.

## Pitfalls

- **Retrying a consequential action blindly.** If `shell` ran a destructive
  command and returned an error, running it again without understanding the
  error can double the damage. Read the error first.
- **Hiding the failure.** Saying "I encountered a small hiccup but kept going"
  when a required step did not complete is dishonest and leaves the user without
  the information they need to decide what to do next.
- **Infinite retry loops.** Structural failures do not become transient with
  repetition. Retry at most once; after that, escalate to the user.
- **Substituting writes for writes.** An alternative tool must be in-bounds and
  non-consequential. Using a different write tool to paper over a write failure
  (e.g. writing to a different path than requested) without telling the user is
  a scope violation.
