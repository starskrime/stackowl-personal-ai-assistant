---
name: schedule-proactive
description: Use when the user wants a recurring goal, reminder, check-in, or proactive notification on a schedule.
when_to_use: When the user asks to be reminded of something, wants a recurring check-in, or wants the assistant to proactively act on a goal at a future time or on a repeating schedule.
version: 0.1.0
tags: [scheduling, proactive, reminders, recurring, cronjob]
author: stackowl-builtin
license: MIT
---

# Schedule a Proactive Goal or Reminder

A request to "remind me every Monday" or "check in on my project weekly"
requires a durable, persisted job — not a promise held only in the current
session. This skill uses the `cronjob` tool to create a real scheduled entry
so the goal fires even after the session ends, and enforces confirmation before
telling the user the schedule is set.

## Steps

1. **Clarify the schedule and goal text if ambiguous.** Before creating the
   job, confirm: (a) the recurrence pattern (e.g. daily at 09:00, every Monday,
   first day of the month); (b) the goal or message text the job should deliver;
   (c) any quiet-hour constraints the user has configured. If any of these are
   unclear, ask before scheduling.

2. **Create the job with the `cronjob` tool.** Pass a valid cron expression
   (or the tool's human-readable schedule format), the goal text, and any
   channel/target parameters. Use a descriptive job name so the user can
   identify it in a job list later.

3. **Confirm the job was created from the tool's result.** Read the response
   from `cronjob` and verify it contains a job ID or explicit success
   confirmation. Do not proceed to Step 4 until this is confirmed.

4. **Tell the user the schedule is set, including the job ID and the next
   expected trigger time.** If the tool returned a next-run timestamp, include
   it. This gives the user something concrete to verify against if the reminder
   does not fire.

## Verification

Before telling the user a schedule is active:

- The `cronjob` tool result must contain a job ID or success status. If it
  reports an error or returns no ID, the job was not created — do not claim
  it was scheduled.
- Repeat the schedule back to the user in plain language (e.g. "every Monday
  at 09:00") so they can catch any misparse of the cron expression before the
  first trigger fires.
- Never say "I've scheduled that" if the tool call has not yet been made or
  returned an error.

## Pitfalls

- **Claiming a schedule that did not persist.** A session-scoped reminder that
  is not backed by a `cronjob` tool call will not fire after the session ends.
  Always use the tool; never simulate scheduling with a conversational promise.
- **Over-frequent schedules.** A job that fires every minute or every few
  seconds can spam the user and exhaust system resources. Validate that the
  interval makes sense for the stated goal before creating it.
- **Ambiguous quiet-hours.** If the user has quiet hours configured, a job
  scheduled during that window may be suppressed or deferred. Surface this
  possibility if the requested time overlaps with likely quiet hours.
- **No job ID returned to the user.** Without a job ID, the user cannot
  identify or cancel the job later. Always include the ID in the confirmation
  message.
- **Duplicate jobs.** Creating the same reminder twice (e.g. because the user
  asked again) creates duplicate noise. Check whether an equivalent job already
  exists before creating a new one.
