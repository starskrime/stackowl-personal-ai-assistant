---
name: deep-research
description: Use when a question needs multiple sources, cross-checked claims, inline citations, and an explicit confidence judgement. Runs several search queries from different angles, fetches the strongest sources, and synthesises a cited answer.
when_to_use: When the user asks a factual question that requires more than one source to answer reliably — especially when the answer may be contested, time-sensitive, or consequential enough to warrant source verification.
version: 0.1.0
tags: [research, web-search, citations, fact-checking]
author: stackowl-builtin
license: MIT
---

# Deep Research with Source Verification

Single-source answers are fragile: sources disagree, pages are outdated, and a
confident-sounding wrong answer is worse than a hedged uncertain one. This skill
enforces a multi-angle search, source-fetching, cross-check, and cite-everything
discipline before any claim reaches the user.

## Steps

1. **Run several `web_search` queries from different angles.** A minimum of
   three queries covering different framings of the question (e.g. the claim
   itself, a counter-claim, a "how do I verify X" angle). Record which queries
   were run so the final reply can list them if asked.

2. **Fetch the strongest sources with `web_fetch`.** Pick the two to four
   results that look most authoritative or most likely to contain the primary
   evidence. Call `web_fetch` on each. If a page requires interaction (login
   prompt, JS-rendered content), use the `browser_navigate` / `browser_snapshot`
   tools to retrieve the rendered content instead.

3. **Cross-check claims across sources.** For each non-obvious claim in the
   draft answer, identify which fetched source supports it. If two sources
   contradict each other, note the discrepancy rather than silently picking one.

4. **Synthesise with inline citations and an explicit confidence level.**
   Write the answer with bracketed inline citations (e.g. `[source: <url>]`
   or `[1]` with a references section). End with a short confidence statement:
   high (multiple independent sources agree), medium (one strong source, others
   indirect), or low (sources conflict or evidence is thin).

## Verification

Before delivering the answer, confirm:

- Every non-obvious claim in the reply is traceable to a URL that was actually
  fetched in this session — not recalled from training data.
- Contradictions between sources are surfaced, not resolved by silent selection.
- The confidence level honestly reflects the source coverage: do not mark
  "high confidence" if only one source was fetched or if sources disagreed.
- If a fetch failed or returned no useful content, the gap is noted rather than
  filled with an unsourced claim.

## Pitfalls

- **Single-source answers.** One search result that looks authoritative is not
  enough. Always cross-check with at least one additional independent source
  before stating a claim as fact.
- **Presenting unverified claims as certain.** Training-data knowledge is a
  starting point for forming queries, not a source. Every claim in the final
  answer must trace to a fetched URL from this session.
- **Fabricated citations.** Do not invent or hallucinate URLs. If a source
  cannot be fetched, say so and lower the confidence rating accordingly.
- **Stale search results.** For time-sensitive questions, check the publication
  or "last updated" date on each fetched source. A result from several years ago
  may be technically reachable but factually outdated.
- **Ignoring contradictions.** When sources disagree, picking one silently
  misleads the user. Surface the disagreement and let the user decide how much
  weight to put on each source.
