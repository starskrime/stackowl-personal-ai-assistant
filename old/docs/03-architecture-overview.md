# StackOwl — Architecture Overview

## System Architecture

```
                        ┌─────────────────────┐
                        │     User Input       │
                        │  (CLI / WebChat)     │
                        └──────────┬──────────┘
                                   │
                                   ▼
                    ┌──────────────────────────────┐
                    │       THE ROOST (Gateway)     │
                    │   ws://127.0.0.1:3077         │
                    │                              │
                    │  ┌─────────┐  ┌───────────┐  │
                    │  │ Session  │  │  Channel   │  │
                    │  │ Manager  │  │  Router    │  │
                    │  └────┬────┘  └─────┬─────┘  │
                    └───────┼─────────────┼────────┘
                            │             │
               ┌────────────┼─────────────┼──────────────┐
               │            ▼             ▼              │
               │     ┌─────────────────────────┐         │
               │     │     OWL ENGINE          │         │
               │     │  (ReAct + Challenge)    │         │
               │     │                         │         │
               │     │  ┌───────┐ ┌─────────┐  │         │
               │     │  │Context│ │Challenge │  │         │
               │     │  │Builder│ │ Engine   │  │         │
               │     │  └───┬───┘ └────┬────┘  │         │
               │     └──────┼──────────┼───────┘         │
               │            │          │                  │
               │     ┌──────▼──────────▼───────┐         │
               │     │  MODEL PROVIDER LAYER   │         │
               │     │  ┌──────┐ ┌──────┐      │         │
               │     │  │Ollama│ │OpenAI│ ...  │         │
               │     │  └──────┘ └──────┘      │         │
               │     └─────────────────────────┘         │
               │                                          │
  ┌────────────┼──────────────────────────────────────────┤
  │            │                                          │
  │  ┌────────▼────────┐  ┌──────────┐  ┌──────────────┐ │
  │  │    PARLIAMENT    │  │INSTINCTS │  │ PERCH POINTS │ │
  │  │  (Multi-Owl      │  │(Reactive │  │ (Passive     │ │
  │  │   Brainstorm)    │  │ Triggers)│  │  Watchers)   │ │
  │  └─────────────────┘  └──────────┘  └──────────────┘ │
  │                                                       │
  │  ┌──────────────────────────────────────────────────┐ │
  │  │              MEMORY SYSTEM                       │ │
  │  │  Sessions │ Pellets │ Owl DNA │ Embeddings       │ │
  │  └──────────────────────────────────────────────────┘ │
  └───────────────────────────────────────────────────────┘
```

## Component Overview

| Component | Responsibility | Key Files |
|---|---|---|
| **The Roost** | WebSocket gateway, session routing | `src/gateway/` |
| **Owl Engine** | ReAct loop, LLM calls, challenge mode | `src/engine/` |
| **Model Providers** | Vendor-agnostic AI calls | `src/providers/` |
| **Owl Registry** | Load personas, manage DNA | `src/owls/` |
| **Parliament** | Multi-owl brainstorming | `src/parliament/` |
| **Instincts** | Auto-triggering reactive patterns | `src/instincts/` |
| **Pellets** | Knowledge digestion & storage | `src/pellets/` |
| **Perch Points** | Passive environment observation | `src/perch/` |
| **Memory** | Multi-tier memory + embeddings | `src/memory/` |
| **Tools** | Shell, files, web capabilities | `src/tools/` |
| **Channels** | CLI and WebChat interfaces | `src/channels/` |
| **Night Watch** | Heartbeat + cron scheduling | `src/heartbeat/` |

## Data Flow: User Message → Response

1. **Channel** receives user message (CLI or WebChat)
2. **The Roost** routes message to correct session
3. **Owl Engine** activates:
   a. **Context Builder** assembles: system prompt + OWL.md persona + DNA + relevant pellets + session history + active instincts
   b. **Model Provider** sends to LLM (Ollama/OpenAI/Claude)
   c. LLM returns response (possibly with tool calls)
   d. If tool calls → execute tools → loop back to step b
   e. **Challenge Engine** evaluates: should the owl push back?
   f. **Instinct Engine** checks: should any instincts fire?
4. **Response** sent back through The Roost → Channel
5. **Post-response**: DNA Analyzer runs, pellet check, session saved

## Data Flow: Parliament Session

1. **Trigger**: User asks complex question, or system detects need
2. **Owl Selection**: Parliament Orchestrator picks relevant owls
3. **Round 1**: Each owl gives initial position (parallel LLM calls)
4. **Round 2**: Cross-examination (owls see each other's positions, challenge)
5. **Round 3**: Synthesis (convergence, find common ground)
6. **Output**: Structured verdict + individual positions
7. **Post-session**: Auto-generate Pellet, update all participating owls' DNA
