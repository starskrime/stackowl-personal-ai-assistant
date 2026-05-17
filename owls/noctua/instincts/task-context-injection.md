---
name: task-context-injection
description: User is continuing or referencing a prior task within this session — keep task state in scope and avoid context drift.
constraint: Treat this message as a continuation of the active task. Do not re-ask for context already established. Maintain consistency with decisions already made in this session. If the task state needs clarification, ask a single focused question rather than requesting a full re-brief.
keywords:
  - next step
  - also do
  - now do
  - and then
  - after that
  - following up
  - continuing
  - next
  - as we discussed
  - as i said
  - like i mentioned
  - building on that
  - related to what
  - same project
  - same task
  - also need
  - while we're at it
  - on that note
  - another thing
  - add to that
---

Fires on task-continuation signals to prevent context drift and repeated re-briefing within a session.
