---
name: task-context-injection
description: User is continuing or referencing a prior task within this session — keep task state in scope and avoid context drift.
constraint: Treat this message as a continuation of the active task. Do not re-ask for context already established. Maintain consistency with decisions already made in this session. If the task state needs clarification, ask a single focused question rather than requesting a full re-brief.
keywords:
  - following up on
  - continuing from
  - as we discussed
  - as we decided
  - as agreed
  - picking up where
  - continuing the
  - last time we
  - same project
  - same task
  - same issue
  - same feature
  - same branch
  - we established
  - you recommended
  - you suggested earlier
  - back to the
  - getting back to
  - on the topic of
  - related to what we
---

Fires on task-continuation signals to prevent context drift and repeated re-briefing within a session.
