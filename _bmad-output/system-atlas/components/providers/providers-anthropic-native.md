---
id: providers-anthropic-native
path: src/providers/anthropic-native.ts
subsystem: providers
type: provider
loc: 418
wired: true
status: mapped
priority: 1
mapped_week: 5
links:
  imports_from:
    - providers-base
  imported_by:
    - providers-manager
---

# src/providers/anthropic-native.ts — AnthropicNativeProvider

> **Status:** mapped · **Wiring:** ✅ wired (via providers-manager)
> **Callers:** 1 · **LoC:** 418 · **Mermaid ID:** `PAN`

## Purpose

Anthropic Claude API provider. Implements `ModelProvider` using `@anthropic-ai/sdk`. Converts StackOwl's internal `ChatMessage[]` format to Anthropic's message structure (enforcing strict role alternation, top-level system parameter, tool_use/tool_result content blocks), then calls the Anthropic API for streaming, non-streaming, and tool-augmented inference. Only provider with direct access to extended thinking and prompt caching.

## Public API

- `AnthropicNativeProvider implements ModelProvider`
  - `chat(messages, model?, options?)` — non-streaming single completion
  - `chatWithTools(messages, tools, model?, options?)` — non-streaming with tool calls
  - `chatWithToolsStream(messages, tools, model?, options?)` — streaming (primary hot path)
  - `chatStream(messages, model?, options?)` — streaming text-only (no tools)
  - `embed()` — throws (Anthropic has no embedding API)
  - `listModels()` — fetches available models from API; falls back to known list
  - `healthCheck()` — lightweight models.list() call; distinguishes network vs. auth errors

## Wiring Status

- **Wired to message path:** yes — instantiated by `providers-manager` when config sets `type: "anthropic"`
- **Active callers:** 1 (providers-manager)

## Abort / Cancellation (2026-05-17)

`chatWithToolsStream` passes `options?.signal` to the Anthropic SDK as a `RequestOptions` argument:

```typescript
const stream = this.client.messages.stream(params, { signal: options?.signal });
```

The SDK wires this signal to an internal abort controller that cancels the HTTP fetch. When `AbortController.abort()` fires in the CLI adapter, the signal propagates through `EngineContext.signal → chatOptions.signal → options?.signal` and reaches here, terminating the network connection immediately.

Without this, the SDK would stream until the response completed regardless of user cancellation.

## Message Conversion Notes

- System messages are concatenated into a single top-level `system` string (Anthropic requirement)
- Tool results become `{ type: "tool_result" }` blocks in a `user` role turn
- Consecutive same-role messages are merged (Anthropic strict alternation)
- If first message is `assistant`, a `(continuing conversation)` user message is prepended

## Data Flow In/Out

- **In:** `ChatMessage[]`, tool definitions (`ToolDefinition[]`), `ChatOptions` (incl. `signal`)
- **Out:** `ChatResponse` (content, toolCalls, usage, model) or `AsyncGenerator<StreamEvent>`
- **StreamEvent types emitted:** `tool_start`, `tool_args_delta`, `tool_end`, `text_delta`, `done`

## Open Questions

- `wired: false` in manifest was incorrect — used by providers-manager. Updated to `true`.

## Cross-references

- [[providers-base]] — `ModelProvider` interface
- [[providers-manager]] — instantiates this provider
- [[engine-runtime]] — `consumeStream` consumes the `AsyncGenerator<StreamEvent>`
- Subsystem rollup: [[providers]]

