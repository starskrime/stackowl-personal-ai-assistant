# CLI Redesign — Component-Based Architecture

**Date:** 2026-04-22  
**Status:** Approved  

## Problem

The current CLI breaks the channel adapter pattern that Telegram and other channels follow. `CLIAdapter` owns `HomeScreen` and `TerminalUI` directly — two 600–1000 line god-classes that duplicate all ANSI helpers, color constants, frame rendering, and popup logic between them. The adapter mixes rendering, input handling, session management, and gateway integration in one tangled blob instead of being a pure transport layer.

## Goal

Make the CLI a clean `ChannelAdapter` — thin transport only — backed by a component-based renderer where each UI piece is an isolated, testable unit.

## Architecture

### Ownership Model

```
CLIAdapter                    ← implements ChannelAdapter (pure transport)
  │  normalizes keys → GatewayMessage
  │  passes GatewayResponse → renderer
  │  no rendering code
  │
  ├─ owns → TerminalRenderer  ← stateful compositor
  │           manages alt-screen lifecycle
  │           owns all components
  │           runs the redraw loop
  │           two modes: home | session
  │
  └─ calls → OwlGateway.handle()
```

### Component Tree

```
TerminalRenderer
  ├─ TopBar          owl badge · model · turn · tokens · cost
  ├─ LeftPanel       home: owl identity + backend info
  │                  session: mind state · DNA bars · tool trace
  ├─ RightPanel      home: recent sessions + centered input
  │                  session: conversation history
  ├─ InputBox        amber-bordered prompt · cursor · history navigation
  ├─ CmdPopup        /command autocomplete overlay
  └─ ShortcutsBar    ESC · ^P · ^L · ^C hints at bottom

InputHandler         ← separate class, owns raw key capture
                       emits: "line" · "quit" · "key" events
                       no rendering — only key processing
```

### File Structure

```
src/cli/
  ├── shared/
  │   ├── ansi.ts          ANSI escape helpers (pos, clear, altIn, altOut…)
  │   ├── palette.ts       AMBER, BLUE, GREEN, MUT, LBL, PANEL_BG, CONTENT_BG
  │   └── text.ts          stripAnsi, visLen, padR, trunc, wrapText
  │
  ├── components/
  │   ├── top-bar.ts
  │   ├── left-panel.ts
  │   ├── right-panel.ts
  │   ├── input-box.ts
  │   ├── cmd-popup.ts
  │   └── shortcuts-bar.ts
  │
  ├── input-handler.ts     raw keystroke capture + input buffer + history
  ├── renderer.ts          compositor — owns components, runs redraw loop
  ├── layout.ts            frame geometry (cols, rows, leftW, rightW)
  ├── commands.ts          unchanged
  ├── onboarding.ts        unchanged
  ├── onboarding-flow.ts   updated: calls renderer methods instead of TerminalUI
  ├── splash.ts            unchanged
  └── owl-art.ts           unchanged
  │
  └── [deleted]
      ├── ui.ts            replaced by renderer + components
      └── home.ts          replaced by renderer home-mode
```

## Data Flow

### Inbound (user types)

```
InputHandler  captures raw stdin keystrokes
    │  buffers chars, cursor, history, backspace
    │  on Enter → emits "line" event
    ▼
CLIAdapter    receives "line"
    │  wraps into GatewayMessage { id, channelId:"cli", userId, sessionId, text }
    │  pushes to serial queue
    │  calls renderer.showThinking()
    ▼
OwlGateway    .handle(msg, callbacks)
    │  callbacks.onStreamEvent → renderer.onStreamChunk(chunk)
    │  callbacks.askInstall    → inputHandler.promptYesNo()
    │                            (InputHandler owns stdin — pauses normal input,
    │                             waits for y/n keypress, then resumes)
    ▼
GatewayResponse  { content, owlName, owlEmoji, toolsUsed, usage }
```

### Outbound (gateway responds)

```
GatewayResponse
    ▼
CLIAdapter    .sendToUser() / .broadcast()
    │  calls renderer.showResponse(response)
    ▼
TerminalRenderer
    │  updates RightPanel state (pushes message to conversation)
    │  updates TopBar state (turn, tokens, cost)
    │  unlocks InputBox
    │  schedules redraw()
    ▼
Components    each renders ANSI string from current props
    ▼
process.stdout.write()   ← only the renderer touches stdout
```

### Streaming

```
OwlGateway fires onStreamEvent per text_delta
    ▼
TerminalRenderer.onStreamChunk(chunk)
    │  appends to RightPanel conversation buffer
    │  schedules redraw() (debounced)
    ▼
RightPanel re-renders with latest buffer
    ▼
stdout — user sees tokens in real time
```

### Redraw Loop

Any state change calls `renderer.redraw()`:
- If already queued → skip (dedupe)
- `setImmediate()` batches multiple changes in one tick
- If already rendering → skip (no re-entrancy)
- Builds full frame string: frame + all components
- Single `process.stdout.write(fullFrame)` per redraw

### Home → Session Transition

Renderer starts in `home` mode. On first submitted message, `renderer.setMode("session")` switches all components to session rendering. No separate `HomeScreen` class — mode is a flag on the renderer.

## Component Contracts

Every component is a **pure function**: plain data props in, ANSI string out. No timers, no event listeners, no `process.stdout` calls, no shared mutable state. The renderer holds all state and calls `render(props)` per frame.

| Component | Signature |
|-----------|-----------|
| TopBar | `render(props: TopBarProps, cols: number): string` |
| LeftPanel | `render(props: LeftPanelProps, width: number, rows: number): string[]` |
| RightPanel | `render(props: RightPanelProps, width: number, rows: number): string[]` |
| InputBox | `render(props: InputBoxProps, width: number): string` |
| CmdPopup | `render(props: CmdPopupProps, width: number): string` |
| ShortcutsBar | `render(props: ShortcutsBarProps, cols: number): string` |

### TopBar Props
- `owlEmoji`, `owlName`, `model`, `turn`, `tokens`, `cost`

### LeftPanel Props
- `mode: "home" | "session"`
- `owlState: "idle" | "thinking" | "done" | "error"`
- `spinIdx: number`
- `dna: { challenge, verbosity, mood }`
- `toolCalls: ToolEntry[]`
- `instincts`, `memFacts`, `skillsHit: number`
- Home-only: `owlEmoji`, `owlName`, `generation`, `challenge`, `provider`, `model`, `skills`

### RightPanel Props
- `mode: "home" | "session"`
- `lines: string[]` (conversation history)
- `scrollOff: number`
- `recentSessions: Array<{ title, turns, ago }>`

### InputBox Props
- `buf: string`, `cursor: number`
- `locked: boolean`, `masked: boolean`
- `spinIdx: number`

### CmdPopup Props
- `matches: string[]`, `selectedIdx: number`
- `startRow: number`, `startCol: number`

### ShortcutsBar Props
- `shortcuts: Array<{ key: string, label: string }>`

## Onboarding Flow Adaptation

`onboarding-flow.ts` currently holds a reference to `TerminalUI` and calls methods like `ui.printLines()`, `ui.printError()`, `ui.setMasked()`, `ui.setAllowEmptyInput()`. In the new design it holds a reference to `TerminalRenderer` and calls equivalent renderer methods. The method signatures remain the same — only the type of the argument changes from `TerminalUI` to `TerminalRenderer`.

## What Changes in CLIAdapter

`src/gateway/adapters/cli.ts` is slimmed to pure transport:
- Constructs `TerminalRenderer` and `InputHandler`
- Wires `InputHandler` "line" events → `gateway.handle()`
- Implements `sendToUser()` / `broadcast()` by calling `renderer.showResponse()`
- Implements `deliverFile()` by calling `renderer.printInfo()`
- No rendering code, no ANSI, no direct stdin/stdout

## What Is Deleted

- `src/cli/ui.ts` — replaced by `renderer.ts` + components
- `src/cli/home.ts` — replaced by renderer home-mode

## Visual Design

Unchanged from current: Neon Accent palette (AMBER primary, BLUE secondary, dark backgrounds), pixel-shadow frame, amber panel separator, two-column layout with left sidebar and right conversation panel.
