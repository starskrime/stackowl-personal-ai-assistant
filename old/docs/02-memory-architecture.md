# StackOwl — Memory & Owl DNA Architecture

## Overview

StackOwl uses a **5-tier memory architecture** inspired by MemGPT/Letta, adapted for the owl metaphor. Memory is the backbone that enables Owl DNA evolution, Pellet generation, and Instinct context awareness.

---

## Memory Tiers

```
┌──────────────────────────────────────────────────┐
│  Tier 1: ACTIVE CONTEXT (In-Context Memory)      │
│  ─ Current conversation window                   │
│  ─ Active owl persona + DNA snapshot              │
│  ─ Loaded instincts relevant to context           │
│  ─ Lives in: LLM context window                   │
├──────────────────────────────────────────────────┤
│  Tier 2: SESSION MEMORY (Episodic)               │
│  ─ Full conversation history for current session │
│  ─ Tool call results and observations            │
│  ─ Lives in: workspace/sessions/<id>.json        │
├──────────────────────────────────────────────────┤
│  Tier 3: PELLETS (Structured Knowledge)          │
│  ─ Compressed insights from conversations        │
│  ─ Parliament verdicts and decisions             │
│  ─ Research findings with evidence               │
│  ─ Lives in: workspace/pellets/<topic>.md        │
├──────────────────────────────────────────────────┤
│  Tier 4: OWL DNA (Behavioral Memory)             │
│  ─ Learned preferences per user-owl pair         │
│  ─ Evolved traits (challenge level, verbosity)   │
│  ─ Expertise growth scores by domain             │
│  ─ Lives in: workspace/owls/<name>/owl_dna.json  │
├──────────────────────────────────────────────────┤
│  Tier 5: EMBEDDING STORE (Semantic Memory)       │
│  ─ Vector embeddings of all pellets & sessions   │
│  ─ Enables semantic search across all memory     │
│  ─ Lives in: workspace/memory/embeddings.json    │
└──────────────────────────────────────────────────┘
```

---

## How Owl DNA Evolves

### The Evolution Loop

```
User Interaction
       │
       ▼
┌─────────────────┐
│  Owl Responds    │ ─── Owl uses current DNA to shape response
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  DNA Analyzer    │ ─── After each interaction, analyze:
│                  │     - Did user accept or override advice?
│                  │     - What topics were discussed?
│                  │     - Was challenge level appropriate?
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  DNA Mutation    │ ─── Small incremental updates (not rewrites):
│                  │     - preference_scores += delta
│                  │     - expertise_growth[topic] += 0.05
│                  │     - challenge_level adjustment
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  DNA Persistence │ ─── Write updated owl_dna.json
│                  │     - Every 5 interactions (batch update)
│                  │     - Version tracked (generation counter)
│                  │     - Inspectable & resettable by user
└─────────────────┘
```

### DNA Data Structure

```json
{
  "owl": "archimedes",
  "generation": 47,
  "created": "2026-03-08T09:50:00Z",
  "last_evolved": "2026-03-08T14:30:00Z",

  "learned_preferences": {
    "prefers_postgresql": 0.85,
    "prefers_typescript": 0.9,
    "tolerance_for_tech_debt": 0.3,
    "values_testing": 0.95,
    "likes_detailed_explanations": 0.7
  },

  "evolved_traits": {
    "challenge_level": "high",
    "verbosity": "concise",
    "humor": 0.4,
    "formality": 0.6
  },

  "expertise_growth": {
    "kubernetes": 0.7,
    "event-driven-architecture": 0.6,
    "postgresql": 0.9,
    "cost-optimization": 0.3
  },

  "interaction_stats": {
    "total_conversations": 142,
    "advice_accepted_rate": 0.73,
    "challenges_given": 89,
    "challenges_accepted": 65,
    "parliament_sessions": 12
  },

  "evolution_log": [
    {
      "generation": 46,
      "timestamp": "2026-03-07T10:00:00Z",
      "mutations": [
        "challenge_level: medium → high (user accepted 8/10 challenges)",
        "expertise_growth.kubernetes: 0.6 → 0.7 (3 k8s discussions)"
      ]
    }
  ]
}
```

### Evolution Rules

| Signal | DNA Mutation | Magnitude |
|---|---|---|
| User accepts owl's challenge | `challenge_level` ↑ | +0.05 |
| User overrides owl's advice | Related preference score ↓ | -0.1 |
| User asks about topic X repeatedly | `expertise_growth[X]` ↑ | +0.05 |
| User says "too verbose" | `verbosity` → more concise | immediate |
| User confirms owl was right | `advice_accepted_rate` ↑ | rolling avg |
| Parliament vote aligns with owl | reinforces owl's stance | +0.03 |

### Safety & Control

- **User can reset DNA**: `stackowl owl reset archimedes` — resets to default
- **User can inspect DNA**: `stackowl owl inspect archimedes` — shows current state
- **User can freeze DNA**: `stackowl owl freeze archimedes` — stops evolution
- **Generation history**: Full evolution log is kept, rollback to any generation
- **Decay**: Preferences decay toward neutral over time if not reinforced (prevents overfit)

---

## How Pellets Work

### Pellet Generation Triggers

1. **End of Parliament session** → Auto-generate decision pellet
2. **Long conversation (>20 messages)** → Summarize key insights into pellet
3. **User explicitly asks** → "Save this as a pellet"
4. **Research session** → When owl gathers information, compress into pellet
5. **Perch Point observation** → Auto-create observation pellet

### Pellet Format

```markdown
---
id: "pel_2026-03-08_microservices-decision"
created: "2026-03-08T14:30:00Z"
source: "parliament"
owls: ["athena", "archimedes", "scrooge"]
tags: ["architecture", "scaling", "cost"]
confidence: 0.85
---

# Microservices vs Monolith Decision

## Context
Team of 8 engineers, ~500k requests/day, PostgreSQL backend.

## Key Insight
Monolith-first with modular boundaries outperforms microservices
at this scale in velocity, cost, and cognitive load.

## Evidence
- Migration effort: ~3 months, 40% data layer rewrite (Archimedes)
- TCO: Monolith 40% cheaper at current scale (Scrooge)
- Module boundaries map 1:1 to future services (Athena)

## Decision
STAY monolith. Revisit at >15 engineers or >50k RPM.

## Related Pellets
- pel_2026-02-15_postgresql-optimization
- pel_2026-01-20_team-scaling-strategy
```

### Pellet Retrieval

When the owl needs context, it:
1. Checks **Tier 1** (active context) — already loaded
2. Searches **Tier 5** (embeddings) — semantic similarity search
3. Loads matching **Tier 3** (pellets) — full structured knowledge
4. Injects relevant pellets into context window

---

## Embedding Strategy

### Approach: Local Embeddings via Provider
- Use whatever provider is configured (Ollama `nomic-embed-text`, OpenAI `text-embedding-3-small`, etc.)
- Generate embeddings for: pellet summaries, session summaries, owl DNA descriptions
- Store as JSON arrays in `workspace/memory/embeddings.json`
- Use cosine similarity for retrieval (no external vector DB needed — keep it simple and file-based)

### When Embeddings Are Generated
- **On pellet creation** → embed the pellet summary
- **On session end** → embed the session summary
- **On DNA evolution** → embed updated expertise descriptions
- **On startup** → verify all embeddings are up-to-date

### Retrieval Flow
```
User Message
     │
     ▼
Generate embedding for user message
     │
     ▼
Cosine similarity search against all stored embeddings
     │
     ▼
Top-K relevant items (pellets, sessions, DNA)
     │
     ▼
Load full content and inject into LLM context
```

---

## Memory Lifecycle

```
Conversation happens
       │
       ├──→ Session saved (Tier 2) immediately
       │
       ├──→ After session: Pellet generated (Tier 3) if insights found
       │
       ├──→ After interaction batch: DNA mutated (Tier 4) every 5 messages
       │
       └──→ Embeddings updated (Tier 5) for new pellets/sessions
```

### Memory Decay
- **Sessions**: Kept indefinitely (small JSON files)
- **Pellets**: Never decay (core knowledge)
- **DNA preferences**: Decay 1% per week toward 0.5 (neutral) if not reinforced
- **Embeddings**: Re-generated when source changes
