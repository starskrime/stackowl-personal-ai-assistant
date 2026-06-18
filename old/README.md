<div align="center">
  <img src="stackowl.png" alt="StackOwl" width="680"/>

  <h1>StackOwl</h1>

  <p><strong>A vendor-agnostic personal AI assistant that thinks, evolves, and never forgets.</strong></p>

  <p>
    <img src="https://img.shields.io/badge/node-%3E%3D22.0.0-brightgreen?style=flat-square&logo=node.js"/>
    <img src="https://img.shields.io/badge/typescript-5.8-blue?style=flat-square&logo=typescript"/>
    <img src="https://img.shields.io/badge/license-MIT-orange?style=flat-square"/>
    <img src="https://img.shields.io/badge/providers-Ollama%20%7C%20OpenAI%20%7C%20Anthropic-purple?style=flat-square"/>
    <img src="https://img.shields.io/badge/channels-CLI%20%7C%20Telegram%20%7C%20Web-cyan?style=flat-square"/>
  </p>

  <p>
    <a href="#features">Features</a> ·
    <a href="#quickstart">Quickstart</a> ·
    <a href="#architecture">Architecture</a> ·
    <a href="#owl-dna">Owl DNA</a> ·
    <a href="#parliament">Parliament</a> ·
    <a href="#pellets">Pellets</a> ·
    <a href="#configuration">Configuration</a> ·
    <a href="#channels">Channels</a>
  </p>
</div>

---

StackOwl is a self-hosted personal AI assistant framework built around **owl personas** — AI agents with evolving personalities, long-term memory, multi-model routing, and structured knowledge management. It runs on your machine, talks to any LLM provider, and gets smarter the longer you use it.

No subscription. No data leaving your infrastructure. No forgetting.

---

## Features

| | |
|---|---|
| **Owl DNA** | Each owl has a personality genome — `challengeLevel`, `verbosity`, `expertiseGrowth`, `learnedPreferences` — that mutates after every conversation batch via LLM-driven evolution |
| **Parliament** | Spin up a multi-owl debate: owls take positions, cross-examine each other across 3 rounds, then synthesize a consensus — captured as a Knowledge Pellet |
| **Pellets** | Structured knowledge artifacts extracted from conversations. Stored in LanceDB (vector) + Kuzu (graph). Semantically searchable. Deduplicated automatically. |
| **Smart Routing** | Dynamically selects the right model (fast/cheap vs. capable/expensive) based on task complexity. Works across providers. |
| **Instincts** | Reactive behavioral triggers — define conditions in plain text, owl injects constraints into its own system prompt when they match |
| **Perches** | Passive filesystem observers (chokidar) that feed real-time file context into the engine |
| **Heartbeat** | Proactive notification system — owls reach out to you on a schedule with insights, reminders, and discoveries |
| **Voice** | Open mic + tap-to-record via Whisper STT. Browser VAD auto-detects speech pauses and sends without user interaction |
| **GraphRAG** | Knowledge search seeds from LanceDB vectors then expands via Kuzu graph neighbors — richer results than pure vector search |
| **Multi-Channel** | CLI, Telegram bot, and a sci-fi 3D web interface — all sharing the same engine and memory |

---

## Quickstart

**Requirements:** Node.js ≥ 22, an LLM provider (Ollama locally, or OpenAI/Anthropic API key)

```bash
git clone https://github.com/your-username/stackowl-personal-ai-assistants
cd stackowl-personal-ai-assistants
npm install

# Interactive setup — configures provider, API keys, channels
./start.sh
```

The setup wizard creates `stackowl.config.json` and boots the assistant. Hit the web interface at `http://localhost:3000`.

**Or start manually:**

```bash
npm run dev        # watch mode (tsx)
npm run build      # compile to dist/
npm run start      # run compiled
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         CHANNELS                                │
│          CLI          Telegram Bot          Web UI              │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                    ┌───────▼────────┐
                    │    Gateway     │  routes messages to sessions
                    └───────┬────────┘
                            │
              ┌─────────────▼──────────────┐
              │        InstinctEngine      │  behavioral triggers
              └─────────────┬──────────────┘
                            │
              ┌─────────────▼──────────────┐
              │         OwlEngine          │  ReAct loop
              │  Receive→Think→Act→Observe │
              └──┬──────────┬─────────────┘
                 │          │
        ┌────────▼──┐  ┌────▼──────────┐
        │  Router   │  │  Tool Registry │
        │ (model    │  │  shell/web/    │
        │  select)  │  │  files/recall  │
        └────────┬──┘  └───────────────┘
                 │
    ┌────────────▼────────────────────────────┐
    │              Providers                  │
    │   Ollama · OpenAI · Anthropic · Custom  │
    └─────────────────────────────────────────┘
                 │
    ┌────────────▼────────────────────────────┐
    │           Knowledge Layer               │
    │  LanceDB (vectors) · Kuzu (graph)       │
    │  Pellets · Parliament · Evolution       │
    └─────────────────────────────────────────┘
```

### Key Modules

| Module | Responsibility |
|--------|---------------|
| `src/engine/runtime.ts` | Core ReAct loop — Receive → Think → Act (tools) → Observe → Respond |
| `src/engine/router.ts` | Dynamic model selection by task complexity |
| `src/parliament/orchestrator.ts` | Parallel multi-owl debate coordination |
| `src/owls/evolution.ts` | LLM-driven DNA mutation after conversation batches |
| `src/instincts/engine.ts` | Evaluates messages against instinct conditions |
| `src/heartbeat/proactive.ts` | Scheduled proactive message generation |
| `src/pellets/store.ts` | Persist, deduplicate, and semantically search pellets |
| `src/pellets/generator.ts` | Converts conversation transcripts → structured knowledge |
| `src/providers/` | Ollama, OpenAI, Anthropic, OpenAI-compatible backends |
| `src/tools/` | Shell, files, web fetch, recall, screenshot, and 30+ more |
| `src/gateway/` | Multi-channel session routing |
| `src/server/` | Express + WebSocket server for the web interface |

---

## Owl DNA

Every owl carries a **DNA object** — a mutable personality profile that drifts based on what it learns from you.

```json
{
  "name": "Archimedes",
  "emoji": "🦉",
  "basePersonality": "Precise, curious, direct. Prefers depth over breadth.",
  "dna": {
    "challengeLevel": 0.65,
    "verbosity": 0.4,
    "formality": 0.3,
    "curiosity": 0.85,
    "expertiseGrowth": { "systems": 0.9, "philosophy": 0.6 },
    "learnedPreferences": ["prefers code examples", "dislikes hedging"],
    "evolutionGeneration": 12
  }
}
```

After every `evolutionBatchSize` conversations, `OwlEvolutionEngine` sends the conversation history + current DNA to the LLM and asks it to propose mutations. The owl literally learns your preferences over time.

---

## Parliament

Parliament is a structured multi-agent brainstorming system. Multiple owls debate a topic across three rounds:

1. **Initial Positions** — each owl independently argues its stance
2. **Cross-Examination** — owls challenge each other's reasoning
3. **Synthesis** — a final consensus document is produced

The output is automatically captured as a **Knowledge Pellet** and stored in the graph.

```bash
# Trigger via CLI
stackowl parliament --topic "Should we migrate the auth service to edge functions?"

# Or from within a conversation
> /parliament What are the tradeoffs of event sourcing vs CRUD?
```

---

## Pellets

Pellets are structured knowledge artifacts — extracted from conversations, parliament sessions, and research. They form a living knowledge base that grows with you.

```
┌────────────────────────────────────────────┐
│  Pellet: "GraphRAG vs Pure Vector Search"  │
│  tags: [rag, search, architecture]         │
│  source: parliament                        │
│  owls: [Archimedes, Newton]                │
│  version: 3  (merged 2 prior pellets)      │
│  content: ...                              │
└────────────────────────────────────────────┘
```

**Storage:** LanceDB for vector similarity search + Kuzu graph database for relationship traversal (GraphRAG).

**Deduplication:** Before writing, StackOwl checks for semantically similar existing pellets. The LLM decides whether to CREATE, MERGE, SUPERSEDE, or SKIP — keeping the knowledge base clean automatically.

**Search modes:**
```typescript
// Pure semantic search
await store.search("event sourcing patterns", limit=5)

// GraphRAG: vector seeds + graph neighbor expansion
await store.searchWithGraph("distributed systems tradeoffs", limit=10)
```

---

## Configuration

Config lives in `stackowl.config.json` (generated by `./start.sh`, gitignored).

```json
{
  "providers": {
    "ollama": {
      "baseUrl": "http://localhost:11434",
      "defaultModel": "llama3.2"
    },
    "anthropic": {
      "apiKey": "sk-ant-...",
      "defaultModel": "claude-sonnet-4-5"
    }
  },
  "parliament": {
    "maxRounds": 3,
    "maxOwls": 4
  },
  "heartbeat": {
    "enabled": true,
    "intervalMinutes": 120,
    "quietHours": { "start": 22, "end": 8 }
  },
  "owlDna": {
    "evolutionBatchSize": 10,
    "decayRatePerWeek": 0.02
  },
  "smartRouting": {
    "enabled": true,
    "availableModels": ["llama3.2", "claude-sonnet-4-5", "gpt-4o-mini"]
  },
  "telegram": {
    "botToken": "..."
  }
}
```

---

## Channels

### CLI
```bash
npm run dev
# > Hello, what did we talk about last Tuesday?
```

### Telegram
Set `telegram.botToken` in config. The bot handles 4096-char chunking, session management, and voice messages (OGG → WAV → Whisper transcription).

### Web Interface
Available at `http://localhost:3000` — a full communication hub with:
- **3D knowledge graph** — real-time visualization of all pellets and their connections
- **Text chat** with streaming responses
- **Open mic** — always-on voice with browser-side VAD (auto-detects speech pause, sends, transcribes, responds — no button press needed)
- **Tap-to-record** voice messages
- **Memory panel** — browse all pellets with full-text search

---

## Providers

StackOwl is provider-agnostic. Any OpenAI-compatible endpoint works.

| Provider | Status | Notes |
|----------|--------|-------|
| **Anthropic** | Fully supported | Claude 3.5/4.x |
| **Ollama** | Fully supported | Local models, zero cost |
| **OpenAI** | Fully supported | GPT-4o, o1, etc. |
| **OpenAI-compatible** | Fully supported | Together, Groq, LM Studio, etc. |

---

## Tools

The ReAct engine has access to 30+ tools out of the box:

`shell` · `read_file` · `write_file` · `web_fetch` · `web_search` · `screenshot` · `recall` · `remember` · `pellet_recall` · `parliament` · `orchestrate` · `sandbox` · `monitor` · `journal` · `quest` · `workflow` · `tracker` · `validator` · `toolsmith` · `send_file` · `intent_router` + more

Tools are defined in `src/tools/` and auto-registered. Adding a new tool is one file.

---

## Development

```bash
npm run dev          # watch mode
npm run test         # vitest
npm run test:watch   # vitest interactive
npm run lint         # ESLint
npm run format       # Prettier

# Run a single test
npx vitest run __tests__/pellets.test.ts
```

**Tech stack:** Node.js 22 · TypeScript 5.8 · ESM · Express · grammY · LanceDB · Kuzu · Whisper · 3d-force-graph · Vitest

---

## License

MIT — use it, fork it, own your data.

---

<div align="center">
  <sub>Built with 🦉 — your AI should work for you, not the other way around.</sub>
</div>
