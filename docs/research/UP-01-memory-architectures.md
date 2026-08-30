# UP-01 — Memory architectures: mem0, the reference platform, and ours

> **Status:** research complete on the LOCAL sources; a decision is Bakir's.
> **Last verified:** 2026-08-30, against commit `9e3de9e2`.
> **Ask:** *"learn architecture from mem0 and discuss about bringing their
> architecture to our memory system"* + *"also research of hermes memory system
> and get winner desing from both"*.
> **Governed by** the standing port-before-build rule: this ends in an explicit
> PORT / HYBRID / BUILD recommendation, and nothing is built before Bakir picks.

## The finding that reframes the question

**The two systems are not independent.** The reference platform's memory is a
*plugin architecture*: `agent/memory_provider.py` (315 lines) defines a
`MemoryProvider` ABC, and `plugins/memory/` holds **eight** implementations —
byterover, hindsight, holographic, honcho, **mem0**, openviking, retaindb,
supermemory. The mem0 adapter is 2,025 lines.

So they already evaluated mem0, and their answer to *"which memory
architecture?"* was **"don't pick one — define the seam."** That position is
itself a candidate for the winner design, and it means the mem0 half of this
study could be read from a real integration rather than from documentation.

## What each one actually does

All rows below were read from source or measured on this machine on 2026-08-30.
Nothing here is from recollection.

| | ours | reference platform | mem0 (as integrated) |
|---|---|---|---|
| store | markdown files, 18 of them | provider's choice | vector store (+ optional graph) |
| how memory reaches the model | **injected into the prompt** as `profile` / `stable_context` parts | `system_prompt_block()` per provider | an **instruction to go search**, not data |
| recall | `memory` tool, `search` action | `prefetch` / `queue_prefetch`, timeout-bounded | speculative prefetch on `on_turn_start` |
| writes | inline tool call | `sync_all` on a **ThreadPoolExecutor**, with `flush_pending(timeout)` | `mem0_add` / `mem0_update` / `mem0_delete` by id |
| at capacity | **`add` REFUSES**, asks the agent to consolidate | provider's problem | update/delete by id |
| failure behaviour | none | `is_available()` | **circuit breaker**, prefetch skipped when open |
| multiple backends | no | **fan-out** — `MemoryManager` runs all providers at once | n/a |

### Measured on our side, 8 days

```
memory tool actions      search 414 | add 119 | replace 62 | remove 12 | forget 1 | get 1
curated writes           82 (59 add + 23 replace) from 210 nudges — 39% conversion
at-capacity refusals     54
decay evictions          40  (until_changed entries only)
committed_facts          0 rows, and committed_facts_fts 0 — dead
```

**I was wrong before measuring this.** I expected we were prompt-injection-first
and rarely searched. `search` is **68% of all memory calls**. We already do
active recall; the gap is not *whether* we recall but *how much it costs*.

## Where each design wins

> **CORRECTED 2026-08-30, after this document was first written.** I ranked
> prefetch first on the strength of 414 synchronous searches. Then I measured
> what those searches *returned*: **0 archive hits on 414 of 414**, and 97%
> returned nothing at all. The archive (`committed_facts`) has 0 rows and no
> writer since seam 3. Prefetching it would deliver nothing faster. The
> round-trip argument below still stands, but the archive's missing writer is
> PRIOR to it — see ESC-69. I had measured the call count and not the yield,
> which is the exact rule this programme runs on, applied to my own advice.

**1. Speculative prefetch — reference platform wins, and it is the biggest win.**
`on_turn_start` fires a background search on the user's message *before* the
model runs; `prefetch()` then consumes the cached result with only a short
hot-path wait. Our 414 searches are all synchronous tool round-trips — the model
must decide to search, emit a call, wait, and read the result, which is a full
extra round including the whole re-sent prefix. This is the single change most
aligned with Bakir's standing concern about burning tokens, and it is measurable:
every prefetched recall is a round-trip that never happens.

**2. Failure containment — reference platform wins.** `is_available()` plus the
mem0 adapter's circuit breaker mean a sick memory backend degrades the answer
instead of breaking the turn. We have no equivalent; our `search` either works or
costs the turn an error. This is the same shape as our standing
no-hidden-errors / self-healing rules, so it is a rule we already hold and have
not applied here.

**3. Writes off the critical path — reference platform wins.** `sync_all` submits
to a ThreadPoolExecutor with an explicit `flush_pending(timeout)` barrier. Our
194 writes are inline: each one costs the user a round-trip in the middle of
their turn. Note this pairs with a real hazard — a background write that
silently fails is exactly the "write with no reader" shape, so the flush barrier
is the part that must be ported, not just the thread pool.

**4. Update/delete by id — mem0 wins on mechanism, ours wins on honesty.** mem0
resolves a conflicting memory by deciding to update or delete it. Ours **refuses
at capacity and asks the agent to consolidate** — 54 times in 8 days. Theirs is
smoother; ours never silently discards something the user said. The 54 is a real
cost (54 turns where the agent got bookkeeping instead of the task) but the
refusal is not a bug, it is a stated design position, and replacing it with an
automatic delete is a *values* change, not an optimisation.

**5. Prompt block as instruction rather than data — mem0 wins.** Their block
spends its tokens teaching the model *when and how* to search — including "do not
assume you have no memory" and "one search is rarely enough" for multi-hop
questions. Ours spends its tokens on the memories themselves. At 18 files ours is
affordable; the instruction form is what scales, and it degrades better because a
missing memory becomes a search rather than an absence.

**6. The plugin seam — nobody wins at our size.** Theirs swaps eight backends
because it is a product with many users. We have one deployment and 18 memory
files. This repo has already shipped abstraction for a scale that never arrived,
and D10.6's premise died when a corpus shrank from 179 skills to 21. Porting the
ABC would be the "simplified implementation always" rule violated on purpose.

## Recommendation — HYBRID, and narrow

Port the **three latency/robustness mechanisms**, not the architecture:

1. **Speculative prefetch** on turn start, timeout-bounded, feeding the existing
   `memory search` path. Biggest measurable token win.
2. **A circuit breaker** around memory reads, so a sick store degrades instead of
   failing the turn.
3. **Background writes with a flush barrier** at the session boundary.

Do **not** port: the provider ABC (no second backend to justify it), the vector
store (18 files), or automatic update/delete (it would overturn a deliberate
position on never discarding what the user said).

Reconsider later, with a trigger rather than a date: if the curated corpus passes
roughly 200 entries, revisit both the vector store and the instruction-style
prompt block — at that size prompt injection stops being affordable and the
at-capacity refusal stops being occasional.

## What is NOT verified

* mem0's **internals** — everything above describes mem0 *as consumed by the
  reference platform's adapter*. Its own extraction/consolidation pipeline was
  not read, so no claim is made about it here.
* Whether our embeddings / Kuzu graph recall paths are live. `committed_facts` is
  measured dead (0 rows, no writer since seam 3, and 0 archive hits across 414
  searches — see ESC-69); the vector and graph stores were NOT audited, so
  "memory recall is dead" would over-claim. What is measured is that the ARCHIVE
  half of the search tool is.
* Numbers for what prefetch would save us. The 414 synchronous searches are
  measured; the saving is an argument, not yet a measurement.

## Verification

```bash
# the eight providers and the ABC
ls do_not_push_to_git_research_only/hermes-agent/plugins/memory/
grep -n "abstractmethod\|def " do_not_push_to_git_research_only/hermes-agent/agent/memory_provider.py

# our action distribution (the number that corrected my framing)
grep -h "memory.execute" ~/.stackowl/logs/stackowl*.jsonl | \
  python3 -c "import sys,json,collections;c=collections.Counter();[c.update([json.loads(l).get('fields',{}).get('action')]) for l in sys.stdin];print(c)"

# committed_facts is dead
sqlite3 ~/.stackowl/workspace/stackowl.db "SELECT COUNT(*) FROM committed_facts"
```
