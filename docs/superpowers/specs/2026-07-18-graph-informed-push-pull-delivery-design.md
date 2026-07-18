# Graph-Informed Push/Pull Delivery — Design (Dynamic-Injection Arc, Sub-project 2 of 4)

> Use sub-project 1's graph (Owl/Skill/Trait nodes) to make skill and DNA-directive
> delivery smarter and self-improving, without changing the existing tier mechanics.
> Skills keep their FULL/SUMMARY/CATALOG system exactly as designed
> (docs/superpowers/specs/2026-06-06-skill-relevance-tiering-design.md) — this adds a
> graph-relevance bonus to the existing score. DNA keeps its existing threshold-latch
> eligibility gate — this adds a relevance RANKING among eligible directives so only the
> top few actually get surfaced, instead of all of them indiscriminately. Two new
> reinforcement events (a skill pull, a post-turn outcome score) close the loop: real usage
> shapes what gets surfaced next time, with no hand-tuning.

**Status:** Design approved (2026-07-18); pending spec re-review
**Builds on:** [[2026-07-18-kuzu-graph-owl-skill-dna-sync-design]] (sub-project 1 — provides
the `Owl`/`Skill`/`Trait` nodes this design adds edges to) and
`2026-06-06-skill-relevance-tiering-design.md` (the existing tiering/floors/hysteresis/pins
system, unchanged mechanically by this design).
**Dynamic-injection arc:** 1 graph schema + sync (done, spec written) → **2 (this)**
graph-informed scoring + pull/outcome feedback loop → 3 DNA directive text generated from
learned traits instead of picked from ~9 fixed sentences (builds on this project's DNA
ranking step) → 4 one combined token budget across all dynamic content.

---

## 1. Problem & approach

Sub-project 1 makes ownership/state queryable as a graph but doesn't change what gets
injected. Today's actual delivery logic has two separate weaknesses this project targets:

- **Skills:** `SkillRelevanceScorer` ranks purely on embedding-cosine-similarity-to-this-
  message plus cross-turn hysteresis (`docs/superpowers/specs/2026-06-06-...`). It has no
  memory of which skills actually *mattered* in similar past situations — a skill can score
  low on raw text similarity even if it's reliably useful whenever a certain kind of
  entity/topic comes up.
- **DNA:** `DNAPromptInjector.inject()` surfaces *every* directive whose trait has crossed
  its latch threshold, with no sense of whether that directive is actually relevant to
  *this* turn's topic — a turn about scheduling a reminder gets the same behavioral
  directives as a turn about writing code, as long as the same traits happen to be latched.

**Decisions (user-approved this session):**
1. Skills' existing tiers/floors/hysteresis/pins stay mechanically unchanged — the graph
   only adds a THIRD bonus term to the score that already feeds `assign_tiers`.
2. DNA's existing latch-eligibility gate stays unchanged — the graph adds a ranking +
   top-K cut among directives that are *already* eligible, not a replacement gate.
3. DNA directives are **always-push, never pull** — behavioral/tone modulation has to
   shape the whole turn from the start; there's no sensible "fetch mid-thought" moment for
   it the way there is for a task-specific skill procedure.
4. Both skills and DNA get their push/pull-adjacent mechanics designed together here
   (DNA's own sub-project, #3, is scoped to the directive TEXT itself, not delivery timing).
5. The two new graph-write triggers reuse EXISTING signal-computation passes
   (`skill_view`'s pull site, and the outcome-scoring pass that already computes skill-
   success/DNA-attribution signals) rather than adding new scoring machinery.

---

## 2. Architecture

Two new weighted edges on sub-project 1's graph, incremented (not overwritten) on repeat:

- **`Skill -RELEVANT_IN-> Entity`** — this skill mattered when this entity was in context.
- **`Trait -RELEVANT_IN-> Entity`** — this directive mattered when this entity was in
  context.

Both are queried the same way: given this turn's recalled entity set (already computed by
`classify`, unchanged), sum the edge weights from a given Skill/Trait to those entities into
a single relevance-bonus float.

**Skills** — `assign_tiers`'s inputs are unchanged; only the score feeding it gains a term:
`final_score = cosine_score + hysteresis_bonus + graph_bonus`. Floors, budget, pins, tier
labels: all identical to the existing design.

**DNA** — `inject()`'s flow becomes: latch-eligibility filter (unchanged) → NEW:
graph-relevance-bonus per eligible directive against this turn's entities → NEW: sort
descending, keep top `MAX_SURFACED_DIRECTIVES` (new constant, default small, e.g. 3 — most
turns won't need more than a couple of behavioral nudges at once) → render exactly as today.
A turn with fewer eligible directives than the cap is unaffected (no padding, mirrors the
skills tiering system's own "budget is a ceiling, not a target" principle).

---

## 3. Reinforcement — where edges get written

**On a skill pull** (`tools/knowledge/skill_view.py`, existing on-demand tool, unchanged
otherwise): after a successful pull, best-effort write/increment `Skill -RELEVANT_IN->
Entity` for every entity in this turn's `memory_context` recall set.

**On post-turn outcome scoring** (existing pass in `reflection_writer_handler.py` /
`CriticScorerHandler` — already computes skill-success attribution and DNA-attribution
signals per the completed Owl DNA lifecycle epic): best-effort write/increment, for a turn
scored as successful, `Skill -RELEVANT_IN-> Entity` for skills actually invoked (a stronger
signal than a mere pull — the skill was used, not just looked at) and `Trait -RELEVANT_IN->
Entity` for directives that were surfaced in that turn. Reuses the entity set and the
success signal this pass already computes — no new scoring logic, just a new write
alongside the existing one.

---

## 4. Error handling

Every graph read (the bonus lookup) and write (edge reinforcement) is best-effort, matching
sub-project 1's rule exactly: a Kuzu failure degrades to the PRE-existing behavior — skills
fall back to cosine+hysteresis only (today's exact scoring), DNA falls back to "surface all
eligible" (today's exact behavior) — never blocks the turn, never raises. This makes the
whole project strictly additive: with Kuzu absent/down, output is byte-identical to before
this project shipped.

---

## 5. Implementation surface

| File | Change |
|---|---|
| `memory/kuzu_adapter.py` | + `link_skill_relevant_to_entity`, `link_trait_relevant_to_entity` (weighted upsert, increment on repeat) + `graph_relevance_bonus(id, turn_entity_ids) -> float` |
| `skills/skill_relevance.py` | `score_owned` adds the graph-bonus term alongside cosine + hysteresis |
| `owls/dna_injector.py` | `inject()` adds the rank-and-cut-to-top-K step after existing latch filtering; new `MAX_SURFACED_DIRECTIVES` constant |
| `tools/knowledge/skill_view.py` | on successful pull, best-effort reinforce `Skill→Entity` edges |
| `reflection_writer_handler.py` / `CriticScorerHandler` | best-effort reinforce `Skill→Entity` (used skills) and `Trait→Entity` (surfaced directives) edges alongside the existing outcome-scoring pass |

---

## 6. Testing

- `graph_relevance_bonus` unit test: fixed edge weights + fixed turn-entity set → expected
  bonus float; missing/no edges → 0 (never negative, never raises).
- Skill scorer unit test: graph bonus combines additively with existing cosine+hysteresis
  fixtures from the 2026-06-06 spec's test suite — extend, don't replace.
- DNA injector unit test: fixed eligible set + fixed relevance scores → correct top-K
  subset surfaced; fewer eligible than K → all surfaced (no padding); a tie-break rule
  (stable, e.g. by trait name) for equal scores.
- Failure-mode tests at all 4 write/read sites: mock `KuzuAdapter` to raise, assert
  byte-identical pre-existing output and a logged warning, never an exception.

---

## 7. Cuts / deferred (tracked)

| Item | Why | Where |
|---|---|---|
| DNA directive pull path | Behavioral/tone modulation can't retroactively apply once a turn has started responding — no sensible pull moment | rejected this round |
| Replacing skills' cosine score with graph score | Graph signal is sparse (only populated by real usage) and cosine still needs to work cold-start / for a never-pulled skill — additive bonus preserves both | kept additive, not a replacement |
| DNA directive TEXT generation from learned trait values | Separate concern (content, not delivery timing) | sub-project 3 |
| A combined token-budget ceiling across all dynamic content | Separate concern (aggregate sizing, not relevance) | sub-project 4 |
