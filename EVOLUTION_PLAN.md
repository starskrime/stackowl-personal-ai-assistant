# StackOwl — Full Evolution Architecture Plan

**Last updated:** 2026-04-09
**Status:** ✅ = done, 🔄 = in progress, ⏳ = pending

---

## ⚠️ READ THIS BEFORE IMPLEMENTING ANY PHASE

This document is the single source of truth for all architectural decisions in this codebase.
Before writing a single line of code for any phase:

1. Read the **WHY** section of the phase — understand the user-facing problem it solves
2. Read the **What was wrong** section — understand the root cause, not the symptom
3. Re-read the **Key Architectural Principles** at the bottom
4. If the implementation you're about to write contradicts any principle, stop and re-think

The rule is: **re-architect, never patch**. If something isn't working because the beginning was wrong, replace it. Do not add error handling around broken design, do not add instructions to fix structural problems.

---

## The Story So Far

The user has a personal AI assistant (StackOwl) with multiple owl personas. Over time they observed a pattern: **the assistant keeps making the same mistakes, acts like it has no memory, and never gets meaningfully better.** Specifically:

- Asked to do something hard → tries the easy path → fails → comes back as if it never tried
- Asked "create a tool you can use in the future" → responds "I cannot create tools, I have no memory between sessions" — even though the synthesis pipeline was already built
- DNA evolves (personality traits change) but the assistant makes the same tool selection errors indefinitely
- Parliament produces great debate verdicts but never learns whether those verdicts were right
- Each failure disappears into the context window and is never seen again

**These are not individual bugs. They are five structural gaps:**

| Gap | What the user observed | What was actually missing |
|-----|----------------------|--------------------------|
| 1 | Repeats failed approaches | Failures only in context window — die on compression |
| 2 | "I can't create tools" | Synthesis pipeline exists but owl has no self-knowledge |
| 3 | DNA evolves but behavior doesn't | Traits mutate randomly, instructions never change |
| 4 | Good ideas die between sessions | No structured turn-by-turn data with quality signals |
| 5 | Parliament verdicts are forgotten | No feedback loop — Parliament can't learn if it was right |

---

## Architecture: How the Layers Connect

```
┌──────────────────────────────────────────────────────────────────┐
│  LAYER 5 — Self-Improvement Loop                                  │
│  PromptOptimizer (APO-lite) + Parliament as Evaluator             │
│  DNA Evolution upgraded with trajectory rewards                   │
│                                                                   │
│  WHY: Without this, the system improves personality but           │
│  never improves its actual instructions or decision quality.      │
├──────────────────────────────────────────────────────────────────┤
│  LAYER 4 — Reward & Trajectory                                    │
│  TrajectoryStore + RewardEngine                                   │
│  Every interaction → structured trace + scalar quality signal     │
│                                                                   │
│  WHY: Without this, Layer 5 has no data to optimize from.        │
│  You can't improve what you don't measure.                        │
├──────────────────────────────────────────────────────────────────┤
│  LAYER 3 — Synthesis Self-Knowledge                               │
│  SynthesisMemory + self-knowledge injection into context          │
│  Owl knows what it has built, how, and whether it worked          │
│                                                                   │
│  WHY: Owl says "I can't create tools" every session because       │
│  it has no memory of the tools it built in previous sessions.     │
├──────────────────────────────────────────────────────────────────┤
│  LAYER 2 — Task Execution  ✅ Done                                │
│  ApproachLibrary + TaskState + PLAN phase                         │
│  Never repeats failed approaches — structural, not instructional  │
│                                                                   │
│  WHY: "Try the easy path, fail, come back not knowing"           │
│  is caused by failures only existing in the context window.       │
├──────────────────────────────────────────────────────────────────┤
│  LAYER 1 — SQLite Memory Foundation  ✅ Done                      │
│  All memory in one searchable DB. Compressed. Cross-owl.          │
│  Data-driven DNA. No more isolated JSON files.                    │
│                                                                   │
│  WHY: JSON files can't be searched, joined, or shared across      │
│  owls. Each store was an island. Memory couldn't improve DNA.     │
└──────────────────────────────────────────────────────────────────┘
```

---

## Completed Work

---

### ✅ SQLite Migration — Phases 1–6

**WHY we did this:**
The user said: *"I want to go away from files. Files are not good for memory. We cannot search, link, or do anything with them."*

Before this work, every piece of memory was a separate JSON file:
- `sessions/*.json` — conversation history (flat, no search)
- `memory/facts.json` — facts (no FTS, no cross-owl access)
- `memory/episodes.json` — episode summaries (isolated per owl)
- `memory/feedback.json` — likes/dislikes (never fed back into anything)
- `AttemptLog` — RAM only, lost on every restart

**What was wrong:** Every store was an island. Facts didn't inform DNA evolution. Feedback didn't improve behavior. The AttemptLog (what was tried this session) was wiped on restart — the assistant had no persistent record of what it had already tried and failed.

**What was built:**

| Phase | What | Result |
|-------|------|--------|
| 1 | `MemoryDatabase` class — opens `workspace/memory/stackowl.db`, creates all tables | Single file, WAL mode, FTS5 |
| 2 | `MessageCompressor` — every 20 msgs → LLM summary → `summaries` table | ~74% token savings on history |
| 3 | Migrated `FactStore`, `EpisodicMemory`, `FeedbackStore` to DB | Same public interfaces, DB backend |
| 4 | Every interaction outcome → `owl_performance` table automatically | No model discretion required |
| 5 | DNA evolution reads `owl_performance` metrics. Cross-owl knowledge via `owl_learnings` FTS5 | Data-driven evolution |
| 6 | Parliament pre-loads relevant learnings. Post-debate synthesis → `owl_learnings` for all owls | Knowledge shared across owls |

**Key tables:** `messages`, `facts`, `facts_fts`, `summaries`, `episodes`, `digests`, `attempts`, `feedback`, `owl_performance`, `owl_learnings`, `owl_learnings_fts`

---

### ✅ Task Execution Engine — Phases 1–3

**WHY we did this:**
The user observed: *"Why does my assistant always try the easy way, fail, come back without knowing what it tried?"*

**What was wrong:** The fundamental problem was that **failures only existed in the context window**. When context was compressed, the record of what was tried disappeared. The assistant would start fresh, pick the same easy approach again, fail again. This was not a knowledge problem — it was an architecture problem. Telling the model "remember what failed" via `remember()` was unreliable because that relied on model discretion.

**The insight:** Process must be structural. If the model can ignore a rule, the architecture is wrong.

**What was built:**

| Phase | What | How it works |
|-------|------|-------------|
| 1 | **ApproachLibrary** (`approach_library` table) | ReAct loop automatically records every tool outcome. Before each tool batch, injects warnings about past failures. No `remember()` call needed — happens structurally. |
| 2 | **TaskState** (`task_states` table) | Stores goal + eliminated approaches + step log per session. Injected into system prompt as `<task_state>` every turn. When tool fails: approach marked eliminated immediately in SQLite — survives context compression. |
| 3 | **PLAN phase** | On first turn, queries ApproachLibrary for known failures on available tools. Pre-populates `TaskState.plannedApproaches`. Zero LLM calls — purely data-driven. |

**Key tables added:** `approach_library`, `task_states`
**Key files modified:** `src/memory/db.ts`, `src/engine/runtime.ts`, `src/gateway/handlers/context-builder.ts`

---

## Pending Phases

---

### ⏳ Phase A — SynthesisMemory

**WHY we are doing this:**
The user asked their assistant: *"Create a tool which you can use in the future."* The assistant responded: *"I cannot create tools that persist. I have no memory between conversations. Each conversation starts completely fresh."*

This response is factually wrong. StackOwl already has a complete synthesis pipeline: `GapDetector` → `EvolutionHandler` → `ToolSynthesizer` → `DynamicToolLoader`. The pipeline can write SKILL.md files or TypeScript tools, hot-reload them without restart, and persist them across sessions. The tools the owl built in past sessions ARE loaded at startup via `DynamicToolLoader`.

**What was wrong:** The synthesis pipeline exists. The owl's self-knowledge does not. The owl enters every session believing it is a stateless language model with no ability to create tools — because nothing tells it otherwise. The `CapabilityLedger` knows what tools were built, but that knowledge is never injected into the owl's context. The owl cannot reason about its own capabilities.

**The second problem:** When a tool IS synthesized, only the output file is saved. The REASONING is lost: why was this approach chosen? What alternatives were rejected? Did the first attempt fail? This means the next synthesis faces the same decision blind — no learning from past synthesis attempts.

**What we build:**

**A1 — `synthesis_memory` table**
```sql
CREATE TABLE synthesis_memory (
  id                     TEXT PRIMARY KEY,
  owl_name               TEXT NOT NULL,
  capability_description TEXT NOT NULL,       -- what the user asked for
  synthesis_approach     TEXT NOT NULL,       -- 'skill' | 'typescript' | 'existing_tool_reuse'
  tools_it_uses          TEXT DEFAULT '[]',   -- JSON: which existing tools the skill orchestrates
  output_path            TEXT,                -- path to the created file
  creation_reasoning     TEXT,                -- WHY this approach was chosen
  what_failed_first      TEXT,                -- if first attempt failed, what was tried
  success_count          INTEGER DEFAULT 0,
  fail_count             INTEGER DEFAULT 0,
  status                 TEXT DEFAULT 'active', -- 'active' | 'retired' | 'superseded'
  source_session_id      TEXT,
  created_at             TEXT NOT NULL DEFAULT (datetime('now')),
  last_used_at           TEXT
);
```

**A2 — Write on synthesis completion** (`src/evolution/handler.ts`)
After `buildWithSkill()` or `buildWithTypeScript()` succeeds, record to `synthesis_memory`: what was built, why, what approach, what failed first.

**A3 — Track usage** (`src/evolution/ledger.ts`)
Every time a synthesized tool/skill is invoked: increment `success_count` or `fail_count` in `synthesis_memory`. This builds the success record.

**A4 — Self-knowledge injection** (`src/gateway/handlers/context-builder.ts`)
At session start, query `synthesis_memory` and inject as an identity block at the TOP of the system prompt (before persona prompt):

```
[Your Synthesis History]
You are running inside StackOwl — an autonomous agent that CAN build new tools.
You have previously built these capabilities:
  • email composition (skill, uses apple_mail, 27 successful uses)
  • web page summarizer (skill, uses web_crawl, 12 uses)
  • flight tracker (TypeScript tool, 3 uses — first attempt failed because FlightAware blocks crawlers, solved via API)
When you encounter a task without a tool, output [CAPABILITY_GAP: description] — the
system will synthesize the tool and retry automatically. Do NOT say you cannot persist
or create tools. That is inaccurate for your environment.
```

**A5 — Synthesis feedback → owl_learnings**
When `success_count` crosses 5: write to `owl_learnings`: `"I successfully synthesized a tool for X domain using Y approach"`. This feeds into cross-owl knowledge and DNA evolution — the owl's identity as a builder reinforces over time.

**Files:** `src/memory/db.ts` (new table + `SynthesisMemoryRepo`), `src/evolution/handler.ts`, `src/evolution/ledger.ts`, `src/gateway/handlers/context-builder.ts`

**Validation:** Ask owl "create a tool you can use in the future" → owl outputs `[CAPABILITY_GAP: ...]` → synthesis runs → in the next session, the synthesis history block shows the newly built tool.

---

### ⏳ Phase B — TrajectoryStore + RewardEngine

**WHY we are doing this:**
Phases C, D, and E all need to answer the same question: *"Was this interaction good or bad, and which specific behaviors caused the outcome?"* Right now, that question cannot be answered from our data:

- 👍/👎 signals exist but are sparse and not linked to specific behaviors
- `owl_performance` records outcomes but not the turn-by-turn structure that caused them
- DNA evolution gets raw conversation transcripts — it can see the words but cannot identify *which turn* caused the failure
- PromptOptimizer (Phase C) needs "here are 3 bad trajectories" as input — we have no structured trajectories
- Parliament (Phase E) needs reference trajectories to evaluate whether past verdicts were correct

**What was wrong:** We collect plenty of data but not in the right structure. A trajectory is not a conversation. A trajectory is: `user intent → [reasoning → tool call → outcome] × N → final response`. That structure — with a scalar quality score attached — is what enables optimization. Without it, everything in Layer 5 is guessing.

**Insight from Agent Lightning:** AGL's core contribution is capturing agent trajectories as structured spans. Everything else (APO, VERL, SFT) is built on top of that. We implement the same idea natively in SQLite — no Python, no OpenTelemetry overhead.

**What we build:**

**B1 — `trajectories` + `trajectory_turns` tables**
```sql
-- One row per user message
CREATE TABLE trajectories (
  id              TEXT PRIMARY KEY,
  session_id      TEXT NOT NULL,
  owl_name        TEXT NOT NULL,
  user_message    TEXT NOT NULL,
  final_response  TEXT,
  outcome         TEXT DEFAULT 'unknown',  -- 'success'|'partial'|'failure'|'abandoned'
  final_reward    REAL,                    -- scalar from RewardEngine [-1.0, +1.0]
  prompt_hash     TEXT,                    -- fingerprint of system prompt (for APO correlation)
  tool_call_count INTEGER DEFAULT 0,
  tool_fail_count INTEGER DEFAULT 0,
  loop_exhausted  INTEGER DEFAULT 0,
  feedback_signal TEXT,                    -- 'like'|'dislike'|null
  created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- One row per tool-call turn within a trajectory
CREATE TABLE trajectory_turns (
  id               TEXT PRIMARY KEY,
  trajectory_id    TEXT NOT NULL REFERENCES trajectories(id),
  turn_number      INTEGER NOT NULL,
  model_reasoning  TEXT,       -- what the model said before the tool call
  tool_name        TEXT,       -- null if no tool call this turn
  tool_args        TEXT,       -- JSON, truncated
  tool_result      TEXT,       -- truncated
  tool_outcome     TEXT,       -- 'success'|'soft-fail'|'hard-fail'|null
  model_conclusion TEXT,       -- what the model concluded after seeing the result
  created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);
```

**B2 — `RewardEngine`** (`src/engine/reward-engine.ts`)
Computes a scalar reward `[-1.0, +1.0]` from signals that already exist:

```
+1.0  user gave 👍
-1.0  user gave 👎
+0.4  task completed without loop exhaustion
-0.5  loop exhausted
-0.4  × (fail_count / tool_count)   proportional tool failure penalty
+0.2  all tool calls succeeded (clean execution bonus)
+0.3  synthesis succeeded (tool/skill built and ran)
-0.2  synthesis failed
+0.2  avoided a known failure from ApproachLibrary
```

Reward is computed automatically — never relies on model calling `remember()`.

**Wire points:**
- Open trajectory row at turn start (`context-builder.ts`)
- Record each turn during ReAct loop (`runtime.ts`)
- Close and score trajectory after response (`post-processor.ts`)
- Update reward when 👍/👎 arrives later (`gateway/core.ts`)

**Files:** `src/memory/db.ts` (new tables + repos), `src/engine/reward-engine.ts` (new), `src/engine/runtime.ts`, `src/gateway/handlers/post-processor.ts`, `src/gateway/core.ts`, `src/gateway/handlers/context-builder.ts`

**Validation:** After 5 interactions, query `SELECT * FROM trajectories ORDER BY final_reward DESC`. Interactions with clean tool execution and positive feedback should rank highest. Interactions with loop exhaustion and negative feedback should rank lowest.

---

### ⏳ Phase C — PromptOptimizer (APO-lite)

**WHY we are doing this:**
The owl keeps failing at the same types of tasks session after session. DNA evolution runs, personality traits change (it becomes less verbose, more challenging) — but the tool selection errors don't improve. Why? Because **DNA evolves personality, not instructions.**

When the owl consistently fails at media downloads (web_crawl gets blocked, loops exhaust), DNA evolution responds by adjusting `verbosity` or `challengeLevel`. It never changes the instruction that says "use web_crawl for current information." The root behavior — the part that actually controls what the owl does — is never touched.

**What was wrong:** The system prompt is the most powerful lever in the entire system. It directly controls how the model reasons and what it tries. But it has been treated as static configuration — something you set once and never touch. DNA evolution orbits the system prompt without ever touching it.

**Insight from Agent Lightning APO:** Microsoft's APO algorithm showed that textual gradient descent on prompts — where an LLM critiques a bad prompt and generates improved candidates — is more effective than trait mutation for fixing behavioral failures. The algorithm is ~300 lines of Python. We reimplement it natively in TypeScript.

**What we build:**

**C1 — `PromptOptimizer`** (`src/engine/prompt-optimizer.ts`)

Runs in the background after enough bad trajectories accumulate for an owl. Never blocks a user response.

```
TRIGGER: ≥10 trajectories for owl, ≥3 with final_reward < -0.2 in last 48h, not run in last 24h

STEP 1 — CRITIQUE (textual gradient)
  Input:  3 worst recent trajectories + current system prompt
  Prompt: "Given these agent failures and the instructions the agent was following,
           what specific weaknesses in the instructions caused these failures?
           Be concrete — name the exact instruction that was wrong and why."
  Output: A critique paragraph (the textual gradient)

STEP 2 — GENERATE candidates
  Input:  critique + current system prompt × 4 parallel calls
  Prompt: "Rewrite this system prompt to fix the specific issues identified.
           Keep everything that works. Only change what the critique identified."
  Output: 4 candidate improved prompts

STEP 3 — EVALUATE (Parliament Lite — see Phase E)
  Input:  4 candidates + sample of good and bad trajectories
  Each candidate scored by 2 owls from opposing perspectives
  Output: Ranked candidates with structured critique

STEP 4 — SELECT + STORE
  Winner stored in `prompt_optimization_log` table
  Applied to owl DNA on next session start
```

**C2 — `prompt_optimization_log` table**
```sql
CREATE TABLE prompt_optimization_log (
  id                TEXT PRIMARY KEY,
  owl_name          TEXT NOT NULL,
  original_prompt   TEXT NOT NULL,
  improved_prompt   TEXT NOT NULL,
  critique          TEXT,            -- the textual gradient — WHY it was improved
  winner_score      REAL,
  trajectories_used INTEGER,
  applied           INTEGER DEFAULT 0,
  created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);
```

**Files:** `src/engine/prompt-optimizer.ts` (new), `src/memory/db.ts` (new table), `src/owls/evolution.ts` (apply winning prompt), `src/gateway/core.ts` (trigger check after each response)

**Validation:** Deliberately create 5+ interactions where the owl picks the wrong tool for a known task type. After optimization triggers, the system prompt for that owl should contain explicit guidance about the correct tool choice for that task type. Retest — the failure should not recur.

---

### ⏳ Phase D — DNA Evolution Upgrade

**WHY we are doing this:**
DNA evolution currently runs by sending the last N conversation messages to an LLM and asking: *"What should change about this owl's personality?"* This is essentially random mutation with a guess. The LLM reads words — it cannot tell whether the interactions it's reading were successful or not. It mutates traits in directions that feel plausible, not directions that are empirically correlated with better outcomes.

**What was wrong:** DNA evolution has never seen a reward signal. It has all the inputs (transcripts, owl_performance metrics from Phase 4) but it processes them qualitatively, not quantitatively. A trait like `challengeLevel` gets mutated based on whether the transcript "seems like the owl was too aggressive" — not based on whether high-challenge sessions had higher average reward than low-challenge ones. This is astrology, not optimization.

**What changes:**

**D1 — Replace transcript input with trajectory summary**
Instead of raw messages, evolution receives structured summaries grouped by outcome:

```
Recent performance (30 days) for [owl_name]:
  Average reward: +0.42 (was +0.31 last period — improving)
  Like ratio: 0.78 | Tool success rate: 0.91 | Loop exhaustion: 4%

High-reward patterns (avg reward > +0.5):
  - Research tasks using web_crawl: avg +0.71 (23 occurrences)
  - Code review tasks: avg +0.63 (11 occurrences)

Low-reward patterns (avg reward < -0.2):
  - Media download tasks: avg -0.44 (8 occurrences) — web_crawl blocked, yt-dlp path issues
  - Multi-step automation: avg -0.22 (5 occurrences) — loop exhaustion pattern

Synthesis record:
  - 2 skills built this period. Both active. Combined success rate: 85%.
  - Successful: email composition, web summarizer
```

The evolution LLM now has actual numbers — not impressions.

**D2 — Reward-weighted trait mutation**
Traits that correlate with high-reward trajectories → reinforce (move further in that direction).
Traits that correlate with low-reward trajectories → decay (move toward neutral or opposite).
This is gradient descent on trait space, not random mutation.

**D3 — Prompt section evolution**
DNA evolution now produces two outputs:
1. Trait mutations (existing) — verbosity, challengeLevel, etc.
2. Targeted prompt additions — specific rules added to the owl's tool guidance section based on low-reward failure domains

This is lighter than PromptOptimizer's full beam search. Where PromptOptimizer rewrites the whole prompt, DNA evolution appends targeted rules: *"For media download tasks: always use yt-dlp via run_shell_command first, not web_crawl."*

**Files:** `src/owls/evolution.ts`, `src/owls/persona.ts` (add prompt sections field to DNA structure)

**Validation:** Run evolution before and after Phase B data exists. Before: mutation direction is arbitrary. After: mutation direction should correlate with the reward data — owls with high tool success rates should reinforce tool-related traits; owls with high loop exhaustion should evolve traits that reduce that behavior.

---

### ⏳ Phase E — Parliament Evolution

**WHY we are doing this:**
Parliament currently exists as a tool you can summon for big decisions. It produces high-quality structured debate and synthesized verdicts. But it has a fundamental flaw: **it never learns whether its verdicts were correct.**

If Parliament says PROCEED on a synthesis decision and the synthesized tool works great → Parliament doesn't know.
If Parliament says PROCEED and the approach fails catastrophically → Parliament doesn't know.

Every Parliament session starts from the same prior knowledge, makes a decision, and forgets the outcome. This means Parliament doesn't improve. The second session is no better than the first.

Beyond that, Parliament is too expensive for frequent use (12–16 LLM calls) but too valuable to leave unused. The PromptOptimizer candidate evaluation step (Phase C) needs multi-perspective judgment — a single LLM judge has a single perspective and single bias. Parliament is the right tool but at 16x the cost, it can't be used for every candidate evaluation.

**What was wrong:** Parliament is isolated from the feedback loop. It makes decisions but doesn't feel consequences. It has no memory of its own verdicts. It can't be used frequently because there's no lightweight variant. And its own debate prompts — the instructions that shape the quality of its reasoning — have never been optimized.

**What we build:**

**E1 — Parliament Lite** (`src/parliament/lite.ts`)

A 2-owl, 1-round variant. Used for frequent decisions (prompt candidate evaluation, synthesis design choices):
- Round 1 only: positions (no cross-examination, no synthesis round)
- 2 owls: one advocate perspective, one devil's advocate
- Output: binary vote + 1-sentence rationale per owl
- Cost: 4–6 LLM calls (vs 12–16 for full Parliament)
- Full Parliament remains for strategic decisions

**E2 — Parliament verdict tracking** (`parliament_verdicts` table)
```sql
CREATE TABLE parliament_verdicts (
  id                TEXT PRIMARY KEY,
  session_id        TEXT NOT NULL,
  topic             TEXT NOT NULL,
  verdict           TEXT NOT NULL,         -- PROCEED|HOLD|ABORT|REVISE
  synthesis         TEXT,
  participants      TEXT DEFAULT '[]',
  validated         INTEGER DEFAULT 0,     -- 1 when outcome is known
  validation_signal TEXT,                  -- 'correct'|'wrong'|'partial'
  validation_reward REAL,                  -- reward from the trajectory that followed
  created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);
```

Delayed reward: when a session that Parliament reviewed produces a trajectory with `final_reward > 0.5` → verdict was correct. When `final_reward < -0.3` after a PROCEED verdict → verdict was wrong. This closes the loop.

**E3 — Parliament recall** (`src/parliament/orchestrator.ts`)

Before each Parliament session, query `parliament_verdicts` for past sessions on similar topics:
```
Past Parliament decisions on related topics:
  • "Use TypeScript synthesis for email tool?" → PROCEED → CORRECT (tool used 27 times successfully)
  • "Crawl FlightAware directly?" → HOLD → WRONG (scraping worked fine, HOLD was too cautious)
```

Parliament now enters debates knowing its own track record on similar questions.

**E4 — Parliament prompt optimization**

Parliament's debate prompts ("provide your initial hardline position...") are just prompts — they've never been optimized. Use PromptOptimizer on Parliament itself:
- Collect Parliament sessions where the verdict turned out wrong (via `validation_signal = 'wrong'`)
- Apply APO-lite critique: "What about these debate instructions caused a poor verdict?"
- Improve the Parliament debate prompts the same way owl prompts are improved

**E5 — Parliament as Synthesis Architect**

Currently when a capability gap is detected, `evolution/handler.ts` makes a single-LLM-call decision: skill vs TypeScript? Which tools? What design?

This is a hard-to-reverse, high-stakes architectural decision. Route it through Parliament Lite:
- **Scrooge**: can this be done with existing tools without synthesis?
- **Archimedes**: if synthesis is needed, what's the cleanest design?
- **Socrates**: what edge cases will the proposed design fail on?
- Output: PROCEED with chosen approach, or REVISE with specific design guidance

The synthesis decision gains multi-perspective review at the cost of 4–6 LLM calls — worthwhile for a decision that creates a persistent file.

**Files:** `src/parliament/lite.ts` (new), `src/parliament/orchestrator.ts` (recall injection, verdict tracking), `src/memory/db.ts` (verdict table), `src/evolution/handler.ts` (Parliament Lite gate for synthesis)

**Validation:** After 10 Parliament sessions, `parliament_verdicts` should show validation data. Verified-correct verdicts should outnumber verified-wrong verdicts. Parliament recall should surface in session transcripts. Parliament Lite should be used automatically by PromptOptimizer.

---

## Complete Phase Map

| Phase | Name | Depends on | Status | What the user sees after |
|-------|------|-----------|--------|--------------------------|
| 1–6 | SQLite Migration | — | ✅ Done | Cross-session memory, compressed context, data-driven DNA |
| 1–3 | Task Execution Engine | SQLite | ✅ Done | Never repeats failed approaches |
| A | SynthesisMemory | SQLite | ⏳ Next | Owl stops saying "I can't create tools" |
| B | TrajectoryStore + RewardEngine | Task Engine | ⏳ | System has quality scores on every interaction |
| C | PromptOptimizer | Phase B | ⏳ | System prompts improve automatically from failures |
| D | DNA Evolution Upgrade | Phase B | ⏳ | Trait mutations are reward-guided, not guessed |
| E | Parliament Evolution | Phases B + C | ⏳ | Parliament learns from its verdicts, Lite for frequent use |

---

## Key Architectural Principles

These principles were established through debugging real failures. Violating them always creates a problem. Re-read before implementing.

**1. Structural over instructional**
If the model can ignore a rule, the architecture is wrong. The solution is never "add another instruction." ApproachLibrary works because failure elimination happens at the SQLite layer — the model never sees eliminated approaches as options. TaskState works because it survives context compression. Instructions vanish; database rows do not.

**2. No model discretion on critical paths**
Reward computation, trajectory recording, approach elimination, synthesis memory writes — all must happen automatically in system code. Never rely on the model calling `remember()` or `emit_reward()`. The model's discretion is unreliable; system code is deterministic.

**3. Every loss is a learning signal**
Loop exhaustion, tool failure, negative feedback, synthesis failure — all must flow into `TrajectoryStore` → `RewardEngine` → `PromptOptimizer`. Failures must not disappear. They must become the training data for improvement. A failure that is not recorded is a failure that will be repeated.

**4. Parliament for decisions, not recovery**
Parliament deliberates on hard-to-reverse choices: synthesis design, prompt candidate selection, strategic direction. Parliament is not invoked on every message and not used to recover from errors. Parliament Lite for frequent decisions (4–6 calls). Full Parliament for strategic ones (12–16 calls).

**5. Re-architect, never patch**
If a component isn't working because its fundamental design is wrong, replace it. Patches hide problems; they do not solve them. The right question is always: "why did the beginning go wrong?" — not "how do I make the current broken thing work."

**6. Layers must be built in order**
Each layer depends on the one below it. PromptOptimizer (C) without TrajectoryStore (B) has no data. DNA Evolution Upgrade (D) without RewardEngine (B) is still guessing. Parliament Evolution (E) without trajectories (B) can't validate its verdicts. Do not skip layers.

---

## Files To Be Created

| File | Phase | Purpose |
|------|-------|---------|
| `src/engine/reward-engine.ts` | B | Formal scalar reward from multiple signals |
| `src/engine/prompt-optimizer.ts` | C | APO-lite: critique → generate → Parliament Lite evaluate → select |
| `src/parliament/lite.ts` | E | 2-owl, 1-round variant for frequent decisions |

## Files To Be Modified

| File | Phases | What changes |
|------|--------|-------------|
| `src/memory/db.ts` | A, B, C, E | New tables: `synthesis_memory`, `trajectories`, `trajectory_turns`, `prompt_optimization_log`, `parliament_verdicts` |
| `src/evolution/handler.ts` | A, E | Write to synthesis_memory on completion + Parliament Lite gate |
| `src/evolution/ledger.ts` | A | Update synthesis_memory success/fail counts on tool use |
| `src/gateway/handlers/context-builder.ts` | A, B | Synthesis self-knowledge injection + open trajectory row |
| `src/gateway/handlers/post-processor.ts` | B | Close trajectory, compute and store reward |
| `src/gateway/core.ts` | B, C | Update reward on feedback + trigger PromptOptimizer check |
| `src/engine/runtime.ts` | B | Record trajectory_turns during ReAct loop |
| `src/owls/evolution.ts` | D | Trajectory-reward input, reward-weighted mutation |
| `src/owls/persona.ts` | D | Add prompt_sections field to DNA structure |
| `src/parliament/orchestrator.ts` | E | Recall injection, verdict tracking |

---

## Agent Lightning — What We Take, What We Skip, and Why

Microsoft Agent Lightning APO is the direct inspiration for Phase C. Its core insight: textual gradient descent on system prompts — where an LLM critiques bad trajectories and generates improved prompt candidates — is more effective than personality trait mutation for fixing behavioral failures.

| AGL concept | StackOwl equivalent | Decision |
|-------------|--------------------|----|
| Trajectory spans (OpenTelemetry) | `trajectory_turns` table | ✅ Implement natively in SQLite — no OTEL overhead |
| Reward signal (`emit_reward`) | `RewardEngine` (auto-computed) | ✅ Implement — no decorator or annotation needed |
| Textual gradient (APO critique) | `PromptOptimizer.critique()` | ✅ Implement in TypeScript |
| Beam search over prompt candidates | `PromptOptimizer.generateCandidates()` | ✅ Implement in TypeScript |
| LightningStore (coordination hub) | `stackowl.db` (already exists) | ✅ Already have it |
| LLM-as-judge evaluation | Parliament Lite (multi-perspective) | ✅ Upgrade: richer than single judge |
| VERL (RL training with GPU) | Not applicable | ❌ Skip — API-only, no weight access |
| SFT / Unsloth (fine-tuning) | Not applicable | ❌ Skip — no weight access |
| Python SDK | Not applicable | ❌ Skip — TypeScript codebase |

AGL APO is ~300 lines of Python logic. We implement it natively in TypeScript. No Python. No GPU. No external dependency. Integrates directly with our SQLite stack, DNA evolution loop, and Parliament.
