---
name: chunked-pdf-summary
description: Summarise a long document by chunking, then merging.
when_to_use: User uploads or references a long document and asks for a summary, brief, key points, or executive overview.
version: 0.1.0
tags: [summarization, long-context, pdf]
author: stackowl-builtin
license: MIT
---

## When to Use
User uploads or references a long document and asks for a summary, brief, key points, or executive overview.

When the user asks for a summary of a document that won't fit cleanly in the
working context, do NOT try to feed the whole thing to the deep tier in one shot.
Use this recursive strategy instead.

## Prerequisites
- A document too long for one context window.
- Access to both a FAST and a DEEP model tier.
- The document's length must be measured before chunking.

## How to Run
1. Detect the document's length.
2. Split it and summarise each chunk with the FAST tier.
3. Recursively re-summarise until the result fits.
4. Run a final consolidating pass on the DEEP tier.

## Quick Reference
- Never feed a whole long document to the deep tier in one shot.
- FAST tier for chunks, DEEP tier only for the final pass.
- Recurse until the intermediate summaries fit, rather than truncating.
- A summary that silently dropped chunks is worse than a shorter one.

## Procedure
1. **Detect length.** If the document has natural section markers (`#`/`##`,
   chapter headers, timestamps, bullet rules), split there. Otherwise split
   on ~3000-token boundaries.

2. **Summarize each chunk with the FAST tier.** Per chunk, produce a 3-bullet
   summary plus a 1-sentence claim about what's most important. Keep speaker /
   page / timestamp markers if present.

3. **Recursively re-summarize.** If the concatenated chunk summaries are still
   > ~2k tokens, repeat step 2 on the summaries themselves. Stop when the
   running summary fits the deliver budget.

4. **Final pass (DEEP tier).** Hand the running summary to the deep tier with
   instructions to: (a) write an exec-style summary scaled to what the user
   actually asked for, (b) preserve verbatim quotes for anything the user is
   likely to cite, (c) note open questions / ambiguities the source left.

## Pitfalls
- If a fast-tier chunk call fails twice, drop that chunk's summary to
  "(unprocessed — chunk N: <one-line topic guess from headers>)" and keep
  going. Never block the final answer on one bad chunk.
- If the deep-tier final pass refuses the length, recursively re-summarize
  one more level instead of truncating.

## Verification
- Every chunk of the source was summarised — none was skipped for length.
- The final pass ran on the DEEP tier, not on the FAST one.
- The summary's claims trace to the document, not to prior knowledge of it.
- Any section that could not be read is named as a gap, not omitted silently.

## Why this beats naive prompting
The deep tier is expensive AND has its own context ceiling. This pattern
pushes the bulk-summarization work onto cheaper / faster providers and
reserves the deep tier for the synthesis the user actually reads.
