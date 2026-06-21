---
name: delegate-or-debate
description: Use when a task is large enough to benefit from a specialist, or when multiple perspectives or a structured debate would produce a better answer than a single pass.
when_to_use: When a task exceeds what a single generalist pass can handle well — either because it needs a specialist owl, because diverse independent drafts improve quality, or because a structured multi-round debate is needed to reach a synthesized position.
version: 0.1.0
tags: [delegation, multi-agent, debate, parliament, specialist]
author: stackowl-builtin
license: MIT
---

# Delegate or Debate

Routing a task to the right handler — a specialist, a mixture of independent
drafts, or a structured debate — produces better outcomes than always answering
in a single generalist pass. This skill provides a DECISION recipe for choosing
among the three modes, then enforces honest reporting of the child result before
it is relayed to the user.

## Steps

1. **Classify the task into one of three modes:**

   - **Single specialist subtask** — the task has a clear domain owner (e.g.
     a coding task, a research task, a writing task) and one specialist owl can
     own it end-to-end. Use `delegate_task`.
   - **Mixture of independent drafts** — the task benefits from several
     independent attempts whose best elements are merged (e.g. creative writing
     variants, alternative plans). Use `mixture_of_agents`.
   - **Multi-round debate to a synthesised position** — the task involves
     a question where diverse perspectives and cross-examination improve the
     final answer (e.g. a consequential decision, a complex trade-off). Run
     Parliament (the built-in multi-owl debate: initial positions → cross-
     examination → synthesis).

   State which mode was chosen and the one-sentence reason before invoking it.

2. **Invoke the chosen mechanism:**
   - `delegate_task`: pass the task description and the target specialist owl
     name (or let the router pick the best match).
   - `mixture_of_agents`: pass the task and the number of independent drafts
     desired; collect the drafts and merge the strongest elements.
   - Parliament: initiate the debate session with the question; collect the
     synthesis output (a Knowledge Pellet) as the result.

3. **Collect and inspect the result.** Confirm the child task or debate
   returned a usable result — not an error, not an empty response, not a
   degraded partial. If the child failed, surface the failure honestly rather
   than manufacturing a substitute answer.

4. **Relay the result to the user**, crediting which mechanism produced it
   (e.g. "the specialist returned…", "the debate synthesised…"). Do not
   present the child result as your own unaided answer.

## Verification

Before relaying the child result:

- Confirm the result is non-empty and coherent — an empty string, a timeout
  error, or a child task that reported failure is not a usable result.
- If the child task returned a degraded result (partial failure, some sub-steps
  failed), surface that degradation honestly rather than presenting the output
  as fully successful.
- Do not relay a child result that was never received (e.g. a fire-and-forget
  call with no response collected). Wait for the response before reporting.

## Pitfalls

- **Delegating trivial work.** The overhead of delegation (routing, context
  transfer, result collection) is not worth it for a simple factual question or
  a one-sentence task. Use delegation for tasks that genuinely benefit from a
  specialist or multi-perspective treatment.
- **Infinite delegation depth.** A specialist owl should not re-delegate the
  same task to another specialist, which can create an infinite loop. Set a
  clear task boundary when delegating.
- **Masking a child failure.** If `delegate_task` or `mixture_of_agents`
  returns an error or a child task fails, do not silently substitute a
  generalist answer and present it as the delegated result. Surface the
  failure and offer to handle it locally or retry.
- **Choosing the wrong mode.** Running a full Parliament debate for a trivial
  question wastes resources and latency. Running a single-specialist delegate
  for a question that genuinely needs diverse perspectives produces a weaker
  answer. Match the mode to the task's actual needs.
