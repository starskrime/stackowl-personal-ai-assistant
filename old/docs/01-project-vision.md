# StackOwl — Project Vision

> 🦉 *"Don't just answer. Challenge. Think. Evolve."*

## What Is StackOwl?

StackOwl is a **vendor-agnostic personal AI assistant** that runs on your laptop. It connects to any AI provider (Ollama, OpenAI, Claude, Google) and features a unique multi-personality "owl" system where specialized AI personas can clone themselves, argue, debate, and collaboratively brainstorm like a real team meeting.

## Core Philosophy

StackOwl doesn't blindly follow orders. Every owl is opinionated — it challenges your assumptions, asks tough questions, and pushes back when it disagrees.

## What Makes StackOwl Different?

### 1. The Parliament (Multi-Owl Brainstorming)
When a problem is complex, StackOwl convenes a Parliament — multiple specialized owls deliberate in structured rounds (position → cross-examination → synthesis) and deliver a verdict with majority/minority opinions.

### 2. Instincts (Not Skills)
Skills are passive — they sit there until called. Instincts are reactive — they fire automatically based on context, events, or observations. Like biological reflexes.

### 3. Pellets (Knowledge Digestion)
Owls digest information and produce Pellets — structured, searchable knowledge artifacts that become long-term memory. Every research session, every Parliament debate produces a Pellet.

### 4. Owl DNA (Personality Evolution)
Owl personas aren't static. Their DNA evolves based on your interactions — learning your preferences, adapting challenge levels, developing expertise in domains you frequently discuss.

### 5. Perch Points (Passive Observation)
Owls don't just respond — they watch. Perch Points are hooks into your file system, git repos, and logs that trigger Instincts when something interesting happens.

## Architecture (Inspired by OpenClaw)

- **The Roost** — WebSocket gateway (control plane)
- **Owl Engine** — ReAct loop + Challenge mode
- **Model Provider Layer** — Vendor-agnostic (Ollama/OpenAI/Claude)
- **Parliament** — Multi-owl brainstorming orchestrator
- **Memory System** — Multi-tiered (session → pellets → long-term → embeddings)

## Tech Stack

- **Runtime**: Node.js ≥ 22, TypeScript
- **AI**: Ollama (remote), OpenAI, Anthropic — any OpenAI-compatible API
- **Storage**: File-based (JSON, Markdown, YAML) — human-readable, version-controllable
- **Interface**: CLI + WebChat
