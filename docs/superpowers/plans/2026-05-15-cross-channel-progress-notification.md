# Cross-Channel Progress Notification System — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract the TUI's 100-language "Working on it…" system into a shared `ProgressNotifier` interface so Telegram (and future channels) get the same rich, multi-language progress indication with per-tool status updates.

**Architecture:** A shared data module (`src/shared/progress.ts`) holds language strings and tool-status phrases. A `ProgressManager` subscribes to `GatewayEventBus` `tool:start` events and fans out `update()` calls to all registered `ProgressNotifier` implementations. Channel adapters call `notifyStart(phrase, turnId)` / `notifyStop(turnId)` directly around `gateway.handle()`.

**Tech Stack:** TypeScript, Node.js, grammY (Telegram), Ink/React (TUI), Vitest, Zustand (TUI state), GatewayEventBus (existing event infrastructure).

---

## File Map

| Action | Path |
|---|---|
| Create | `src/shared/progress.ts` |
| Modify | `src/cli/v2/components/spinner.ts` |
| Create | `src/progress/types.ts` |
| Create | `src/progress/manager.ts` |
| Create | `src/progress/index.ts` |
| Modify | `src/gateway/core.ts` |
| Create | `src/progress/notifiers/telegram.ts` |
| Modify | `src/gateway/adapters/telegram.ts` |
| Add event types | `src/cli/v2/events/UiEvent.ts` |
| Modify | `src/cli/v2/state/slices/ui.ts` |
| Modify | `src/cli/v2/components/ThinkingIndicator.tsx` |
| Create | `src/progress/notifiers/tui.ts` |
| Modify | `src/gateway/adapters/cli-v2.ts` |
| Create | `src/progress/notifiers/slack.ts` |
| Create | `src/progress/notifiers/websocket.ts` |
| Create | `src/progress/README.md` |
| Create | `__tests__/progress/manager.test.ts` |
| Create | `__tests__/progress/notifiers/telegram.test.ts` |
| Create | `__tests__/progress/notifiers/tui.test.ts` |

---

## Task 1: Shared Progress Foundation

**Files:**
- Create: `src/shared/progress.ts`
- Modify: `src/cli/v2/components/spinner.ts`
- Create: `src/progress/types.ts`
- Create: `src/progress/manager.ts`
- Create: `src/progress/index.ts`
- Test: `__tests__/progress/manager.test.ts`

- [ ] **Step 1: Write the failing test for ProgressManager**

Create `__tests__/progress/manager.test.ts`:

```typescript
import { describe, it, expect, vi, beforeEach } from "vitest";
import { ProgressManager } from "../../src/progress/manager.js";
import type { ProgressNotifier } from "../../src/progress/types.js";
import type { GatewayEventBus } from "../../src/gateway/event-bus.js";

type BusHandler = (e: { type: string; [k: string]: unknown }) => void;

function makeEventBus() {
  const handlers = new Map<string, BusHandler[]>();
  return {
    on(type: string, handler: BusHandler) {
      if (!handlers.has(type)) handlers.set(type, []);
      handlers.get(type)!.push(handler);
    },
    trigger(event: { type: string; [k: string]: unknown }) {
      for (const h of handlers.get(event.type) ?? []) h(event);
    },
  } as unknown as GatewayEventBus & { trigger: (e: { type: string; [k: string]: unknown }) => void };
}

function makeNotifier(): ProgressNotifier & { calls: string[] } {
  const calls: string[] = [];
  return {
    calls,
    async start(phrase: string, turnId: string) { calls.push(`start:${turnId}:${phrase}`); },
    async update(text: string, turnId: string) { calls.push(`update:${turnId}:${text}`); },
    async stop(turnId: string) { calls.push(`stop:${turnId}`); },
  };
}

describe("ProgressManager", () => {
  let bus: ReturnType<typeof makeEventBus>;
  let manager: ProgressManager;

  beforeEach(() => {
    bus = makeEventBus();
    manager = new ProgressManager(bus as unknown as GatewayEventBus);
  });

  it("fans out notifyStart to all registered notifiers", async () => {
    const a = makeNotifier();
    const b = makeNotifier();
    manager.register(a);
    manager.register(b);
    await manager.notifyStart("Working on it...", "turn-1");
    expect(a.calls).toEqual(["start:turn-1:Working on it..."]);
    expect(b.calls).toEqual(["start:turn-1:Working on it..."]);
  });

  it("fans out notifyStop to all registered notifiers", async () => {
    const a = makeNotifier();
    manager.register(a);
    await manager.notifyStop("turn-1");
    expect(a.calls).toEqual(["stop:turn-1"]);
  });

  it("fans out tool:start events as update() calls", async () => {
    const a = makeNotifier();
    manager.register(a);
    bus.trigger({ type: "tool:start", toolName: "shell", args: {}, turnId: "turn-1" });
    await new Promise((r) => setImmediate(r)); // flush async
    expect(a.calls[0]).toMatch(/^update:turn-1:/);
    expect(a.calls[0]).toContain("turn-1");
  });

  it("does not fan out to unregistered notifiers", async () => {
    const a = makeNotifier();
    manager.register(a);
    manager.unregister(a);
    await manager.notifyStart("phrase", "turn-1");
    expect(a.calls).toHaveLength(0);
  });

  it("session isolation: tool:start with different turnId reaches all notifiers (they filter internally)", async () => {
    const a = makeNotifier();
    manager.register(a);
    bus.trigger({ type: "tool:start", toolName: "web_fetch", args: {}, turnId: "turn-X" });
    await new Promise((r) => setImmediate(r));
    // ProgressManager delivers to all notifiers — internal filtering is each notifier's job
    expect(a.calls.some((c) => c.includes("turn-X"))).toBe(true);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/progress/manager.test.ts
```

Expected: FAIL — `Cannot find module '../../src/progress/manager.js'`

- [ ] **Step 3: Create `src/shared/progress.ts`**

```typescript
/**
 * Shared progress-notification data.
 * Used by all channels — no TUI-specific imports allowed here.
 */

export const STACKOWL_SPINNER = ["·", "◌", "◍", "◉", "✳", "✶"] as const;

/** Yellow → red → yellow fade palette for the thinking animation. */
export const FADE_COLORS = [
  "#F5A623", "#F59020", "#F57418", "#F55810",
  "#FF4444",
  "#F55810", "#F57418", "#F59020",
] as const;

/** Interval between language rotations (ms). */
export const LANG_INTERVAL_MS = 4000;

/** "Working on it..." in 100 languages. */
export const THINKING_MESSAGES = [
  "Working on it...",
  "Trabajando en ello...",
  "Je m'en occupe...",
  "Ich arbeite daran...",
  "Ci sto lavorando...",
  "Trabalhando nisso...",
  "Работаю над этим...",
  "取り組んでいます...",
  "正在处理...",
  "작업 중...",
  "أعمل على ذلك...",
  "इस पर काम कर रहा हूं...",
  "Üzerinde çalışıyorum...",
  "Bezig met werken...",
  "Pracuję nad tym...",
  "Arbetar på det...",
  "Jobber med det...",
  "Arbejder på det...",
  "Työskentelen sen parissa...",
  "Εργάζομαι πάνω σε αυτό...",
  "Pracuji na tom...",
  "Dolgozom rajta...",
  "Lucrez la asta...",
  "Працюю над цим...",
  "Đang xử lý...",
  "กำลังดำเนินการ...",
  "Sedang mengerjakan...",
  "עובד על זה...",
  "Ninafanya kazi...",
  "Üzərində işləyirəm...",
  "Sedang bekerja...",
  "Nagtatrabaho sa ito...",
  "এটি নিয়ে কাজ করছি...",
  "ਇਸ 'ਤੇ ਕੰਮ ਕਰ ਰਿਹਾ ਹਾਂ...",
  "இதில் வேலை செய்கிறேன்...",
  "దీనిపై పని చేస్తున్నాను...",
  "यावर काम करत आहे...",
  "اس پر کام کر رہا ہوں...",
  "در حال کار روی آن هستم...",
  "በዚህ ላይ እየሰራሁ ነው...",
  "Mo n ṣiṣẹ lori rẹ...",
  "Ina aiki akan shi...",
  "Ana m arụ ọrụ na ya...",
  "Ngisebenza kulo...",
  "Ndisebenza kulo...",
  "Werk daaraan...",
  "Treballant en això...",
  "Horretan lanean nago...",
  "Traballando niso...",
  "Yn gweithio ar hynny...",
  "Ag obair air...",
  "Er að vinna í því...",
  "Dirbu ties tuo...",
  "Strādāju pie tā...",
  "Töötan selle kallal...",
  "Delam na tem...",
  "Pracujem na tom...",
  "Radim na tome...",
  "Радим на томе...",
  "Работам на тоа...",
  "Работя по това...",
  "Po punoj për të...",
  "ვმუშაობ ამაზე...",
  "Աշխատում եմ դրա վրա...",
  "Жұмыс жасап жатырмын...",
  "Bu ustida ishlayapman...",
  "Bu üstünde işleýärin...",
  "Иштеп жатам...",
  "Дар ин кор мекунам...",
  "Үүн дээр ажиллаж байна...",
  "यसमा काम गर्दैछु...",
  "ဒါကို လုပ်နေသည်...",
  "កំពុងធ្វើការលើវា...",
  "ກຳລັງດຳເນີນການ...",
  "正在處理...",
  "Lagi nggarap iki...",
  "Keur ngerjakeun ieu...",
  "Nagbuhat niini...",
  "Waxaan u shaqeynayaa...",
  "Miasa amin'izany aho...",
  "Ke a sebetsa ho sona...",
  "Ndikugwira ntchito pa ichi...",
  "Ndimo gukora kuri iyo...",
  "Ndiri kushanda pari zvino...",
  "Laborante pri ĝi...",
  "Opere incumbo...",
  "अस्मिन् कार्यं करोमि...",
  "Qed naħdem fuqha...",
  "Schaffe drun...",
  "O labourat war se...",
  "Trabalhando...",
  "Kwa hili ninafanya kazi...",
  "Mimi ni kufanya kazi...",
  "Está se a trabalhar...",
  "Treballem en això...",
  "Je travaille dessus...",
  "Ich bin dran...",
  "Sto lavorando...",
  "Estoy en ello...",
  "На этом работаю...",
  "처리 중입니다...",
  "考えています...",
] as const;

/** Returns a random "Working on it…" phrase from the 100-language pool. */
export function pickRandomPhrase(): string {
  return THINKING_MESSAGES[Math.floor(Math.random() * THINKING_MESSAGES.length)]!;
}

/**
 * Short human-readable status phrases per tool name.
 * Used by all channels during tool execution.
 */
export const TOOL_STATUS_PHRASES: Record<string, string> = {
  shell:                  "🐚 Running command…",
  read_file:              "📄 Reading file…",
  write_file:             "✏️  Writing file…",
  web_fetch:              "🌐 Fetching page…",
  web_search:             "🔍 Searching the web…",
  browser_navigate:       "🌐 Navigating browser…",
  browser_control:        "🖥️  Controlling browser…",
  read_logs:              "📋 Reading logs…",
  memory_search:          "🧠 Searching memory…",
  memory_write:           "🧠 Writing to memory…",
  list_files:             "📁 Listing files…",
  grep:                   "🔍 Searching files…",
  image_generate:         "🎨 Generating image…",
  calendar_event:         "📅 Checking calendar…",
  email_send:             "📧 Sending email…",
};

/** Returns a tool status phrase, or a generic fallback for unknown tools. */
export function getToolStatusPhrase(toolName: string): string {
  return TOOL_STATUS_PHRASES[toolName] ?? "⚙️  Working…";
}
```

- [ ] **Step 4: Update `src/cli/v2/components/spinner.ts` to re-export from shared**

Replace the entire file content:

```typescript
/** TUI v2 spinner constants. Shared data re-exported from src/shared/progress.ts. */
import { colors } from "../theme/tokens.js";

// Re-export shared progress data so existing imports continue to work unchanged.
export {
  THINKING_MESSAGES,
  STACKOWL_SPINNER,
  FADE_COLORS,
  LANG_INTERVAL_MS,
  pickRandomPhrase,
  TOOL_STATUS_PHRASES,
  getToolStatusPhrase,
} from "../../../shared/progress.js";

/** Spinner icon color — sourced from the brand design token. */
export const SPINNER_AMBER = colors.brand;
/** Raw spinner frame interval (ms). */
export const SPINNER_INTERVAL_MS = 80;
/** Slower interval for tool call cards (ms). */
export const TOOL_SPIN_INTERVAL_MS = 150;
/** Interval for the thinking indicator spinner (ms). */
export const THINKING_SPIN_INTERVAL_MS = 250;
```

- [ ] **Step 5: Create `src/progress/types.ts`**

```typescript
/**
 * ProgressNotifier — the interface every channel implements.
 *
 * The rendering strategy (spinner, typing indicator, ACK message, etc.)
 * is entirely the channel's concern. The contract is just these three
 * lifecycle methods, all scoped by turnId.
 */
export interface ProgressNotifier {
  /**
   * Called once when a turn begins.
   * phrase is a random "Working on it…" string from pickRandomPhrase().
   */
  start(phrase: string, turnId: string): Promise<void>;

  /**
   * Called when a tool starts executing.
   * text is a short human-readable status from getToolStatusPhrase().
   */
  update(text: string, turnId: string): Promise<void>;

  /**
   * Called when the turn is fully complete and the final answer has been sent.
   */
  stop(turnId: string): Promise<void>;
}
```

- [ ] **Step 6: Create `src/progress/manager.ts`**

```typescript
import { log } from "../logger.js";
import type { GatewayEventBus } from "../gateway/event-bus.js";
import type { ProgressNotifier } from "./types.js";
import { getToolStatusPhrase } from "../shared/progress.js";

/**
 * ProgressManager — subscribes to GatewayEventBus and fans out progress
 * events to all registered ProgressNotifier implementations.
 *
 * Channel adapters:
 *   1. register(notifier) at startup
 *   2. call notifyStart(phrase, turnId) before gateway.handle()
 *   3. call notifyStop(turnId) after gateway.handle() resolves
 *
 * tool:start events are intercepted automatically and fanned out as update().
 */
export class ProgressManager {
  private notifiers = new Set<ProgressNotifier>();

  constructor(eventBus: GatewayEventBus) {
    log.engine.debug("progress-manager: init");

    eventBus.on("tool:start", (e) => {
      log.engine.debug("progress-manager: tool:start", { toolName: e.toolName, turnId: e.turnId });
      const phrase = getToolStatusPhrase(e.toolName);
      void this._fanOutUpdate(phrase, e.turnId);
    });
  }

  register(notifier: ProgressNotifier): void {
    log.engine.debug("progress-manager: register", { total: this.notifiers.size + 1 });
    this.notifiers.add(notifier);
  }

  unregister(notifier: ProgressNotifier): void {
    log.engine.debug("progress-manager: unregister", { total: this.notifiers.size - 1 });
    this.notifiers.delete(notifier);
  }

  async notifyStart(phrase: string, turnId: string): Promise<void> {
    log.engine.debug("progress-manager: notifyStart", { turnId, phraseLen: phrase.length });
    await Promise.allSettled(
      [...this.notifiers].map((n) =>
        n.start(phrase, turnId).catch((err) => {
          log.engine.error("progress-manager: notifyStart fan-out error", err as Error, { turnId });
        }),
      ),
    );
  }

  async notifyStop(turnId: string): Promise<void> {
    log.engine.debug("progress-manager: notifyStop", { turnId });
    await Promise.allSettled(
      [...this.notifiers].map((n) =>
        n.stop(turnId).catch((err) => {
          log.engine.error("progress-manager: notifyStop fan-out error", err as Error, { turnId });
        }),
      ),
    );
  }

  private async _fanOutUpdate(text: string, turnId: string): Promise<void> {
    await Promise.allSettled(
      [...this.notifiers].map((n) =>
        n.update(text, turnId).catch((err) => {
          log.engine.error("progress-manager: fanOutUpdate error", err as Error, { turnId });
        }),
      ),
    );
  }
}
```

- [ ] **Step 7: Create `src/progress/index.ts`**

Start with only the files that exist in Task 1. Notifier exports are added by each subsequent task.

```typescript
export type { ProgressNotifier } from "./types.js";
export { ProgressManager } from "./manager.js";
```

- [ ] **Step 8: Run the tests**

```bash
npx vitest run __tests__/progress/manager.test.ts
```

Expected: All 5 tests PASS.

- [ ] **Step 9: Verify build compiles**

```bash
npm run build
```

Expected: No errors. (The barrel `index.ts` exports notifiers that don't exist yet — comment them out temporarily if needed, restore after Task 5.)

- [ ] **Step 10: Commit**

```bash
git add src/shared/progress.ts src/cli/v2/components/spinner.ts src/progress/types.ts src/progress/manager.ts src/progress/index.ts __tests__/progress/manager.test.ts
git commit -m "feat(progress): shared foundation — ProgressNotifier interface and ProgressManager"
```

---

## Task 2: Expose ProgressManager on OwlGateway

**Files:**
- Modify: `src/gateway/core.ts`

Context: `OwlGateway` already exposes `getProviderManager()` as a lazy singleton at line ~3298. Add `getProgressManager()` following the exact same pattern. The class field `readonly gatewayEventBus: GatewayEventBus = new GatewayEventBus()` is declared at line ~223.

- [ ] **Step 1: Add the field and getter to OwlGateway**

In `src/gateway/core.ts`, add the import near the top with other manager imports:

```typescript
import { ProgressManager } from "../progress/manager.js";
```

Add the private field alongside `_providerManager` (around line 332):

```typescript
private _progressManager?: ProgressManager;
```

Add the getter alongside `getProviderManager()` (around line 3315, after `getProviderManager`):

```typescript
getProgressManager(): ProgressManager {
  if (!this._progressManager) {
    log.engine.debug("owl-gateway.getProgressManager: initialized");
    this._progressManager = new ProgressManager(this.gatewayEventBus);
  }
  return this._progressManager;
}
```

- [ ] **Step 2: Verify build**

```bash
npm run build
```

Expected: No errors.

- [ ] **Step 3: Commit**

```bash
git add src/gateway/core.ts
git commit -m "feat(progress): expose ProgressManager singleton on OwlGateway"
```

---

## Task 3: TelegramProgressNotifier

**Files:**
- Create: `src/progress/notifiers/telegram.ts`
- Modify: `src/gateway/adapters/telegram.ts`
- Test: `__tests__/progress/notifiers/telegram.test.ts`

Context: The Telegram adapter's text message handler (around line 527) currently:
1. Sends `sendChatAction("typing")` once
2. Picks from a hardcoded 5-phrase English `ACK_MESSAGES` array and sends as an italic message
3. Passes `ackMessageId` to `createStreamHandler` so streaming edits that message

After this task:
1. `TelegramProgressNotifier.start()` does both steps above
2. `TelegramProgressNotifier.update()` edits the ACK message in-place with tool status
3. `createStreamHandler` gains an optional `onStreamClaimed` callback, fired when streaming starts editing the ACK message
4. `TelegramProgressNotifier.stop()` clears the typing loop; if stream never claimed the message, deletes the ACK

- [ ] **Step 1: Write failing tests**

Create `__tests__/progress/notifiers/telegram.test.ts`:

```typescript
import { describe, it, expect, vi, beforeEach } from "vitest";
import { TelegramProgressNotifier } from "../../../src/progress/notifiers/telegram.js";

function makeApi() {
  const calls: Array<{ method: string; args: unknown[] }> = [];
  const api = {
    calls,
    sendMessage: vi.fn(async (_chatId: number, _text: string, _opts?: unknown) => {
      calls.push({ method: "sendMessage", args: [_chatId, _text, _opts] });
      return { message_id: 42 };
    }),
    sendChatAction: vi.fn(async (_chatId: number, _action: string) => {
      calls.push({ method: "sendChatAction", args: [_chatId, _action] });
    }),
    editMessageText: vi.fn(async (_chatId: number, _msgId: number, _text: string, _opts?: unknown) => {
      calls.push({ method: "editMessageText", args: [_chatId, _msgId, _text, _opts] });
    }),
    deleteMessage: vi.fn(async (_chatId: number, _msgId: number) => {
      calls.push({ method: "deleteMessage", args: [_chatId, _msgId] });
    }),
  };
  return api;
}

describe("TelegramProgressNotifier", () => {
  let api: ReturnType<typeof makeApi>;
  let notifier: TelegramProgressNotifier;

  beforeEach(() => {
    vi.useFakeTimers();
    api = makeApi();
    notifier = new TelegramProgressNotifier(api as never);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("start() sends ACK message and starts typing loop", async () => {
    notifier.bindSession("turn-1", 100);
    await notifier.start("Working on it...", "turn-1");

    expect(api.sendMessage).toHaveBeenCalledWith(100, "<i>Working on it...</i>", { parse_mode: "HTML" });
    expect(api.sendChatAction).toHaveBeenCalledWith(100, "typing");

    // Typing loop fires after 4000ms
    await vi.advanceTimersByTimeAsync(4000);
    expect(api.sendChatAction).toHaveBeenCalledTimes(2);
  });

  it("update() edits the ACK message", async () => {
    notifier.bindSession("turn-1", 100);
    await notifier.start("phrase", "turn-1");
    await notifier.update("🐚 Running command…", "turn-1");

    expect(api.editMessageText).toHaveBeenCalledWith(100, 42, "🐚 Running command…", { parse_mode: "HTML" });
  });

  it("stop() clears timer and deletes ACK when stream not claimed", async () => {
    notifier.bindSession("turn-1", 100);
    await notifier.start("phrase", "turn-1");
    await notifier.stop("turn-1");

    expect(api.deleteMessage).toHaveBeenCalledWith(100, 42);
    // Timer should be cleared — no more typing actions after stop
    await vi.advanceTimersByTimeAsync(8000);
    expect(api.sendChatAction).toHaveBeenCalledTimes(1); // only the initial one
  });

  it("stop() does NOT delete ACK when stream has claimed it", async () => {
    notifier.bindSession("turn-1", 100);
    await notifier.start("phrase", "turn-1");
    notifier.markStreamClaimed("turn-1");
    await notifier.stop("turn-1");

    expect(api.deleteMessage).not.toHaveBeenCalled();
  });

  it("ignores unknown turnId in update() and stop()", async () => {
    await notifier.update("text", "unknown-turn");
    await notifier.stop("unknown-turn");
    expect(api.editMessageText).not.toHaveBeenCalled();
    expect(api.deleteMessage).not.toHaveBeenCalled();
  });

  it("getAckMessageId() returns the sent message ID", async () => {
    notifier.bindSession("turn-1", 100);
    await notifier.start("phrase", "turn-1");
    expect(notifier.getAckMessageId("turn-1")).toBe(42);
  });

  it("concurrent sessions are isolated", async () => {
    notifier.bindSession("turn-A", 100);
    notifier.bindSession("turn-B", 200);
    await notifier.start("phrase-A", "turn-A");
    await notifier.start("phrase-B", "turn-B");

    expect(api.sendMessage).toHaveBeenCalledWith(100, "<i>phrase-A</i>", { parse_mode: "HTML" });
    expect(api.sendMessage).toHaveBeenCalledWith(200, "<i>phrase-B</i>", { parse_mode: "HTML" });
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/progress/notifiers/telegram.test.ts
```

Expected: FAIL — `Cannot find module '../../../src/progress/notifiers/telegram.js'`

- [ ] **Step 3: Create `src/progress/notifiers/telegram.ts`**

```typescript
import { log } from "../../logger.js";
import type { ProgressNotifier } from "../types.js";

interface Session {
  chatId: number;
  messageId: number | undefined;
  timer: ReturnType<typeof setInterval> | null;
  streamClaimed: boolean;
}

// Minimal subset of grammY Api used here — avoids importing the full grammY type.
interface TelegramApi {
  sendMessage(chatId: number, text: string, opts?: { parse_mode?: string }): Promise<{ message_id: number }>;
  sendChatAction(chatId: number, action: string): Promise<unknown>;
  editMessageText(chatId: number, messageId: number, text: string, opts?: { parse_mode?: string }): Promise<unknown>;
  deleteMessage(chatId: number, messageId: number): Promise<unknown>;
}

/**
 * TelegramProgressNotifier — implements ProgressNotifier for the Telegram channel.
 *
 * Lifecycle per session (turnId):
 *   1. Adapter calls bindSession(turnId, chatId) to register the chat.
 *   2. ProgressManager calls start(phrase, turnId):
 *      - Sends an italic ACK message in the random language.
 *      - Starts a 4-second setInterval to refresh sendChatAction("typing").
 *   3. ProgressManager calls update(text, turnId) for each tool:
 *      - Edits the ACK message with the tool status text.
 *   4. Adapter calls markStreamClaimed(turnId) when the stream handler takes over the ACK message.
 *   5. ProgressManager calls stop(turnId):
 *      - Clears the typing refresh interval.
 *      - Deletes the ACK message unless stream claimed it (in which case it's the response).
 */
export class TelegramProgressNotifier implements ProgressNotifier {
  private sessions = new Map<string, Session>();

  constructor(private api: TelegramApi) {}

  /** Register chatId for a turnId before calling start(). */
  bindSession(turnId: string, chatId: number): void {
    log.telegram.debug("telegram-progress-notifier: bindSession", { turnId, chatId });
    this.sessions.set(turnId, {
      chatId,
      messageId: undefined,
      timer: null,
      streamClaimed: false,
    });
  }

  /** Called by the stream handler when it takes over the ACK message for streaming output. */
  markStreamClaimed(turnId: string): void {
    const s = this.sessions.get(turnId);
    if (s) {
      log.telegram.debug("telegram-progress-notifier: stream claimed", { turnId });
      s.streamClaimed = true;
    }
  }

  /** Returns the Telegram message ID of the sent ACK message, or undefined if not yet sent. */
  getAckMessageId(turnId: string): number | undefined {
    return this.sessions.get(turnId)?.messageId;
  }

  async start(phrase: string, turnId: string): Promise<void> {
    const session = this.sessions.get(turnId);
    if (!session) {
      log.telegram.warn("telegram-progress-notifier: start called without bindSession", { turnId });
      return;
    }

    log.telegram.debug("telegram-progress-notifier: start", { turnId, chatId: session.chatId });

    // Send initial ACK message in the random language.
    try {
      const sent = await this.api.sendMessage(
        session.chatId,
        `<i>${escHtml(phrase)}</i>`,
        { parse_mode: "HTML" },
      );
      session.messageId = sent.message_id;
    } catch (err) {
      log.telegram.warn("telegram-progress-notifier: start: sendMessage failed", err as Error, { turnId });
    }

    // Send initial typing action, then refresh every 4000ms.
    try {
      await this.api.sendChatAction(session.chatId, "typing");
    } catch (err) {
      log.telegram.warn("telegram-progress-notifier: start: sendChatAction failed", err as Error, { turnId });
    }

    session.timer = setInterval(() => {
      this.api.sendChatAction(session.chatId, "typing").catch((err) => {
        log.telegram.warn("telegram-progress-notifier: typing refresh failed", err as Error, { turnId });
      });
    }, 4000);
  }

  async update(text: string, turnId: string): Promise<void> {
    const session = this.sessions.get(turnId);
    if (!session?.messageId) return;
    if (session.streamClaimed) return; // stream owns the message now

    log.telegram.debug("telegram-progress-notifier: update", { turnId, text });

    try {
      await this.api.editMessageText(session.chatId, session.messageId, text, {
        parse_mode: "HTML",
      });
    } catch (err) {
      log.telegram.warn("telegram-progress-notifier: update: editMessageText failed", err as Error, { turnId });
    }
  }

  async stop(turnId: string): Promise<void> {
    const session = this.sessions.get(turnId);
    if (!session) return;

    log.telegram.debug("telegram-progress-notifier: stop", { turnId, streamClaimed: session.streamClaimed });

    if (session.timer) {
      clearInterval(session.timer);
      session.timer = null;
    }

    // Delete the ACK message only if the stream never claimed it.
    // If claimed, the message now contains the response — do not delete.
    if (!session.streamClaimed && session.messageId) {
      try {
        await this.api.deleteMessage(session.chatId, session.messageId);
      } catch (err) {
        log.telegram.warn("telegram-progress-notifier: stop: deleteMessage failed", err as Error, { turnId });
      }
    }

    this.sessions.delete(turnId);
  }
}

function escHtml(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
npx vitest run __tests__/progress/notifiers/telegram.test.ts
```

Expected: All 7 tests PASS.

- [ ] **Step 5: Wire TelegramProgressNotifier into `src/gateway/adapters/telegram.ts`**

**5a. Add imports** near the top of telegram.ts (after existing imports):

```typescript
import { v4 as uuidv4 } from "uuid";
import { TelegramProgressNotifier } from "../../progress/notifiers/telegram.js";
import { pickRandomPhrase } from "../../shared/progress.js";
```

(Note: `uuid` is already a dependency — check the existing imports for `uuidv4` usage. If not present, it's available via the `uuid` package already in package.json.)

**5b. Add `_progressNotifier` field** in the `TelegramAdapter` class body (alongside other private fields):

```typescript
private _progressNotifier!: TelegramProgressNotifier;
```

**5c. Initialize in constructor** (after `this.bot` is constructed, before any other setup):

```typescript
// Progress notifier — registered on the shared ProgressManager
this._progressNotifier = new TelegramProgressNotifier(this.bot.api as never);
gateway.getProgressManager().register(this._progressNotifier);
```

**5d. Replace the ACK/typing block in the text message handler.**

Find the existing block (around line 527–557):
```typescript
await ctx.api.sendChatAction(ctx.chat.id, "typing");
// ...
const ACK_MESSAGES = [ ... ];
const ackText = ACK_MESSAGES[...];
let ackMessageId: number | undefined;
try {
  const ackMsg = await ctx.api.sendMessage(...);
  ackMessageId = ackMsg.message_id;
} catch (err) { ... }
```

Replace the entire block (from `await ctx.api.sendChatAction` through the closing catch) with:

```typescript
const turnId = uuidv4();
this._progressNotifier.bindSession(turnId, ctx.chat.id);
await this.gateway.getProgressManager().notifyStart(pickRandomPhrase(), turnId);
const ackMessageId = this._progressNotifier.getAckMessageId(turnId);
```

**5e. Update the `createStreamHandler` call** (around line 562) to pass `onStreamClaimed`:

```typescript
const streamCtx = this.createStreamHandler(
  ctx,
  this.gateway.getConfig().gateway?.suppressThinkingMessages ?? true,
  ackMessageId,
  () => this._progressNotifier.markStreamClaimed(turnId),
);
```

**5f. Call `notifyStop` after `gateway.handle()` resolves.**

Find the line after `const response = await runWithContext(...)` resolves (around line 615, after the `gateway.handle()` call). Add immediately after the closing `));` of `runWithContext`:

```typescript
await this.gateway.getProgressManager().notifyStop(turnId);
```

Wrap in try/catch to prevent stop failures from hiding response errors:
```typescript
try {
  await this.gateway.getProgressManager().notifyStop(turnId);
} catch (err) {
  log.telegram.warn("telegram: notifyStop failed", err as Error, { turnId });
}
```

**5g. Update `createStreamHandler` signature** to accept the `onStreamClaimed` callback:

Find the method signature (around line 1103):
```typescript
private createStreamHandler(
  ctx: Context,
  suppressThinking: boolean,
  initialMessageId?: number,
):
```

Change to:
```typescript
private createStreamHandler(
  ctx: Context,
  suppressThinking: boolean,
  initialMessageId?: number,
  onStreamClaimed?: () => void,
):
```

Inside the handler, in the `text_delta` case, add a one-time claim trigger. Find where content is first appended to `displayText` (around line 1202). Before that line, add:

```typescript
// On first non-empty chunk, notify that the stream has claimed the message.
if (chunk && !streamClaimedFired) {
  streamClaimedFired = true;
  onStreamClaimed?.();
}
```

And declare the flag near the top of `createStreamHandler` (alongside `editFailures`):

```typescript
let streamClaimedFired = false;
```

- [ ] **Step 6: Verify build**

```bash
npm run build
```

Expected: No TypeScript errors.

- [ ] **Step 7: Commit**

```bash
git add src/progress/notifiers/telegram.ts src/gateway/adapters/telegram.ts __tests__/progress/notifiers/telegram.test.ts
git commit -m "feat(progress): TelegramProgressNotifier — multi-language ACK, typing loop, tool status"
```

---

## Task 4: TUI Notifier

**Files:**
- Modify: `src/cli/v2/events/UiEvent.ts`
- Modify: `src/cli/v2/state/slices/ui.ts`
- Modify: `src/cli/v2/components/ThinkingIndicator.tsx`
- Create: `src/progress/notifiers/tui.ts`
- Modify: `src/gateway/adapters/cli-v2.ts`
- Test: `__tests__/progress/notifiers/tui.test.ts`

Context: The TUI already shows `ThinkingIndicator` when `generating: true`. This task wires the notifier so (a) the bridge-supplied phrase overrides the random fallback, and (b) tool status text updates the indicator during tool execution.

- [ ] **Step 1: Write failing tests**

Create `__tests__/progress/notifiers/tui.test.ts`:

```typescript
import { describe, it, expect, vi } from "vitest";
import { TuiProgressNotifier } from "../../../src/progress/notifiers/tui.js";
import type { UiBridge } from "../../../src/cli/v2/events/bridge.js";
import type { UiEvent } from "../../../src/cli/v2/events/UiEvent.js";

function makeBridge() {
  const emitted: UiEvent[] = [];
  return {
    emitted,
    emit: vi.fn((event: UiEvent) => { emitted.push(event); }),
  } as unknown as UiBridge & { emitted: UiEvent[] };
}

describe("TuiProgressNotifier", () => {
  it("start() emits thinking.phrase event", async () => {
    const bridge = makeBridge();
    const notifier = new TuiProgressNotifier(bridge);
    await notifier.start("Trabajando en ello...", "turn-1");
    expect(bridge.emitted).toContainEqual({
      kind: "thinking.phrase",
      turnId: "turn-1",
      phrase: "Trabajando en ello...",
    });
  });

  it("update() emits thinking.tool event when turnId is active", async () => {
    const bridge = makeBridge();
    const notifier = new TuiProgressNotifier(bridge);
    await notifier.start("phrase", "turn-1");
    await notifier.update("🐚 Running command…", "turn-1");
    expect(bridge.emitted).toContainEqual({
      kind: "thinking.tool",
      turnId: "turn-1",
      text: "🐚 Running command…",
    });
  });

  it("update() is a no-op for unknown turnId", async () => {
    const bridge = makeBridge();
    const notifier = new TuiProgressNotifier(bridge);
    await notifier.update("text", "unknown");
    expect(bridge.emitted).toHaveLength(0);
  });

  it("stop() clears phrase with empty thinking.phrase", async () => {
    const bridge = makeBridge();
    const notifier = new TuiProgressNotifier(bridge);
    await notifier.start("phrase", "turn-1");
    await notifier.stop("turn-1");
    const phraseEvents = bridge.emitted.filter((e) => e.kind === "thinking.phrase");
    // Last thinking.phrase event should have empty phrase
    expect(phraseEvents.at(-1)).toMatchObject({ kind: "thinking.phrase", phrase: "" });
  });

  it("stop() is a no-op for unknown turnId", async () => {
    const bridge = makeBridge();
    const notifier = new TuiProgressNotifier(bridge);
    await notifier.stop("unknown");
    expect(bridge.emitted).toHaveLength(0);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run __tests__/progress/notifiers/tui.test.ts
```

Expected: FAIL — `Cannot find module '../../../src/progress/notifiers/tui.js'`

- [ ] **Step 3: Add new UiEvent types to `src/cli/v2/events/UiEvent.ts`**

After the `PromptSubmittedEvent` interface (around line 307), add:

```typescript
// ─── Progress notification ─────────────────────────────────────────────────────

/** Emitted by TuiProgressNotifier.start() to set the phrase in ThinkingIndicator. */
export interface ThinkingPhraseEvent {
  kind: "thinking.phrase";
  turnId: string;
  /** The random-language "Working on it…" phrase. Empty string clears the override. */
  phrase: string;
}

/** Emitted by TuiProgressNotifier.update() to show tool status under the spinner. */
export interface ThinkingToolEvent {
  kind: "thinking.tool";
  turnId: string;
  text: string;
}
```

Then add both to the `UiEvent` union (after `PromptSubmittedEvent`):

```typescript
  | ThinkingPhraseEvent
  | ThinkingToolEvent;
```

- [ ] **Step 4: Update `src/cli/v2/state/slices/ui.ts`**

Add `thinkingPhrase` to `UiSliceState`:

```typescript
export interface UiSliceState {
  // ... existing fields ...
  /** Phrase override for ThinkingIndicator, supplied by TuiProgressNotifier. Null = use random fallback. */
  thinkingPhrase: string | null;
}
```

Add to `initialUiSliceState`:

```typescript
thinkingPhrase: null,
```

In `applyUiEvent`, inside the `switch`, add two new cases before `default`:

```typescript
case "thinking.phrase":
  return { ...state, thinkingPhrase: event.phrase || null };

case "thinking.tool":
  return { ...state, thinkingPhrase: event.text };
```

Also, in the existing `turn.committed` case, clear the phrase on completion. Find the return statement in `turn.committed` and add `thinkingPhrase: null`:

```typescript
case "turn.committed": {
  // ...existing logic...
  return { ...state, generating: false, totalTokens: tokens, totalCostUsd: cost, contextWindowPct, thinkingPhrase: null };
}
```

- [ ] **Step 5: Update `src/cli/v2/components/ThinkingIndicator.tsx`**

Replace the file content:

```typescript
import { useState, useEffect } from "react";
import { Box, Text } from "ink";
import {
  STACKOWL_SPINNER,
  THINKING_SPIN_INTERVAL_MS,
  SPINNER_AMBER,
  FADE_COLORS,
  THINKING_MESSAGES,
} from "./spinner.js";
import { useUiStore } from "../providers/UiStoreProvider.js";

/**
 * Animated "Working on it..." indicator shown while the owl is thinking.
 * Spinner icon blinks on the left; text is sourced from the ProgressNotifier
 * (via thinkingPhrase in the store) or falls back to a random language.
 */
export function ThinkingIndicator() {
  const [spinFrame, setSpinFrame] = useState(0);
  const [fallbackIdx] = useState(() => Math.floor(Math.random() * THINKING_MESSAGES.length));
  const thinkingPhrase = useUiStore((s) => s.thinkingPhrase);

  useEffect(() => {
    const t = setInterval(
      () => setSpinFrame((f) => (f + 1) % STACKOWL_SPINNER.length),
      THINKING_SPIN_INTERVAL_MS,
    );
    return () => clearInterval(t);
  }, []);

  const color = FADE_COLORS[spinFrame % FADE_COLORS.length];
  const displayPhrase = thinkingPhrase ?? THINKING_MESSAGES[fallbackIdx]!;

  return (
    <Box>
      <Text color={SPINNER_AMBER}>{STACKOWL_SPINNER[spinFrame]} </Text>
      <Text bold color={color}>{displayPhrase}</Text>
    </Box>
  );
}
```

- [ ] **Step 6: Create `src/progress/notifiers/tui.ts`**

```typescript
import { log } from "../../logger.js";
import type { UiBridge } from "../../cli/v2/events/bridge.js";
import type { ProgressNotifier } from "../types.js";

/**
 * TuiProgressNotifier — thin adapter from ProgressNotifier to UiBridge events.
 *
 * The TUI's spinner (ThinkingIndicator) already renders when generating:true.
 * This notifier adds two signals:
 *   - thinking.phrase: overrides the random language pick with the notifier-supplied phrase
 *   - thinking.tool:  shows tool status text while a tool is running
 *
 * start/stop do NOT emit turn.started / turn.committed — those are handled
 * by the existing cli-v2 adapter path and must not be duplicated.
 */
export class TuiProgressNotifier implements ProgressNotifier {
  private activeTurnIds = new Set<string>();

  constructor(private bridge: UiBridge) {}

  async start(phrase: string, turnId: string): Promise<void> {
    log.engine.debug("tui-progress-notifier: start", { turnId });
    this.activeTurnIds.add(turnId);
    this.bridge.emit({ kind: "thinking.phrase", turnId, phrase });
  }

  async update(text: string, turnId: string): Promise<void> {
    if (!this.activeTurnIds.has(turnId)) return;
    log.engine.debug("tui-progress-notifier: update", { turnId, text });
    this.bridge.emit({ kind: "thinking.tool", turnId, text });
  }

  async stop(turnId: string): Promise<void> {
    if (!this.activeTurnIds.has(turnId)) return;
    log.engine.debug("tui-progress-notifier: stop", { turnId });
    this.activeTurnIds.delete(turnId);
    // Clear the phrase override so ThinkingIndicator reverts to random fallback next time.
    this.bridge.emit({ kind: "thinking.phrase", turnId, phrase: "" });
  }
}
```

- [ ] **Step 7: Run tests to verify they pass**

```bash
npx vitest run __tests__/progress/notifiers/tui.test.ts
```

Expected: All 5 tests PASS.

- [ ] **Step 8: Wire TuiProgressNotifier into `src/gateway/adapters/cli-v2.ts`**

**8a. Add imports** at the top (after existing imports):

```typescript
import { TuiProgressNotifier } from "../../progress/notifiers/tui.js";
import { pickRandomPhrase } from "../../shared/progress.js";
```

**8b. Add `_progressNotifier` field** in `CliV2Adapter`:

```typescript
private _progressNotifier!: TuiProgressNotifier;
```

**8c. Initialize in constructor** (after `this._quitPromise` is constructed):

```typescript
this._progressNotifier = new TuiProgressNotifier(globalBridge);
gateway.getProgressManager().register(this._progressNotifier);
```

**8d. Call `notifyStart` in `submitMessage()`**, immediately after the existing `globalBridge.translateOwlChange(...)` call (around line 174):

```typescript
// Announce progress via ProgressManager (sets ThinkingIndicator phrase).
void this._gateway.getProgressManager().notifyStart(pickRandomPhrase(), turnId);
```

(Use `void` since this is fire-and-forget — the indicator updates asynchronously.)

**8e. Call `notifyStop` after the response is complete.** In `submitMessage()`, the response is handled in a `try/catch`. After the `if (!committedViaStream)` block (around line 233) and before the closing `catch`, add:

```typescript
void this._gateway.getProgressManager().notifyStop(turnId);
```

Also add in the `catch` block (to ensure stop is called even on error), after the existing error log:

```typescript
void this._gateway.getProgressManager().notifyStop(turnId);
```

- [ ] **Step 9: Verify build and run full test suite**

```bash
npm run build && npm run test
```

Expected: Build succeeds. Tests pass (no regressions).

- [ ] **Step 10: Commit**

```bash
git add src/cli/v2/events/UiEvent.ts src/cli/v2/state/slices/ui.ts src/cli/v2/components/ThinkingIndicator.tsx src/progress/notifiers/tui.ts src/gateway/adapters/cli-v2.ts __tests__/progress/notifiers/tui.test.ts
git commit -m "feat(progress): TuiProgressNotifier — bridge phrase and tool status to ThinkingIndicator"
```

---

## Task 5: Stub Notifiers + README

**Files:**
- Create: `src/progress/notifiers/slack.ts`
- Create: `src/progress/notifiers/websocket.ts`
- Create: `src/progress/README.md`

- [ ] **Step 1: Create `src/progress/notifiers/slack.ts`**

```typescript
import { log } from "../../logger.js";
import type { ProgressNotifier } from "../types.js";

/**
 * SlackProgressNotifier — stub implementation.
 * Full implementation pending Slack channel adapter buildout.
 *
 * Expected behavior when implemented:
 *   start()  → add a reaction emoji (e.g. ⏳) to the user's message
 *   update() → post/update an ephemeral status message in the thread
 *   stop()   → remove the reaction emoji
 */
export class SlackProgressNotifier implements ProgressNotifier {
  async start(_phrase: string, turnId: string): Promise<void> {
    log.engine.debug("slack-progress-notifier: start (stub — not implemented)", { turnId });
  }

  async update(_text: string, turnId: string): Promise<void> {
    log.engine.debug("slack-progress-notifier: update (stub — not implemented)", { turnId });
  }

  async stop(turnId: string): Promise<void> {
    log.engine.debug("slack-progress-notifier: stop (stub — not implemented)", { turnId });
  }
}
```

- [ ] **Step 2: Create `src/progress/notifiers/websocket.ts`**

```typescript
import { log } from "../../logger.js";
import type { ProgressNotifier } from "../types.js";

/**
 * WebSocketProgressNotifier — stub implementation.
 * Full implementation pending WebSocket channel adapter buildout.
 *
 * Expected behavior when implemented:
 *   start()  → push { type: "thinking", phrase } to the connected client
 *   update() → push { type: "tool", text } to the connected client
 *   stop()   → push { type: "done" } to the connected client
 */
export class WebSocketProgressNotifier implements ProgressNotifier {
  async start(_phrase: string, turnId: string): Promise<void> {
    log.engine.debug("websocket-progress-notifier: start (stub — not implemented)", { turnId });
  }

  async update(_text: string, turnId: string): Promise<void> {
    log.engine.debug("websocket-progress-notifier: update (stub — not implemented)", { turnId });
  }

  async stop(turnId: string): Promise<void> {
    log.engine.debug("websocket-progress-notifier: stop (stub — not implemented)", { turnId });
  }
}
```

- [ ] **Step 3: Update `src/progress/index.ts` barrel** to add the stub notifiers:

```typescript
export type { ProgressNotifier } from "./types.js";
export { ProgressManager } from "./manager.js";
export { TelegramProgressNotifier } from "./notifiers/telegram.js";
export { TuiProgressNotifier } from "./notifiers/tui.js";
export { SlackProgressNotifier } from "./notifiers/slack.js";
export { WebSocketProgressNotifier } from "./notifiers/websocket.js";
```

- [ ] **Step 4: Create `src/progress/README.md`**

```markdown
# Progress Notification System

Provides a unified "working on it" progress indication across all channels.

## Interface

```typescript
interface ProgressNotifier {
  start(phrase: string, turnId: string): Promise<void>;
  update(text: string, turnId: string): Promise<void>;
  stop(turnId: string): Promise<void>;
}
```

## Adding a new channel

1. Create `src/progress/notifiers/<channel>.ts` implementing `ProgressNotifier`.
2. In your channel adapter constructor, call:
   ```typescript
   const notifier = new YourProgressNotifier(...);
   gateway.getProgressManager().register(notifier);
   ```
3. Before `gateway.handle()`, call:
   ```typescript
   const turnId = uuidv4();
   notifier.bindSession(turnId, ...channelSpecificData);
   await gateway.getProgressManager().notifyStart(pickRandomPhrase(), turnId);
   ```
4. After `gateway.handle()` resolves, call:
   ```typescript
   await gateway.getProgressManager().notifyStop(turnId);
   ```

## Shared data

- `src/shared/progress.ts` — 100-language phrases, tool status map, utilities
- `pickRandomPhrase()` — returns a random language phrase
- `getToolStatusPhrase(toolName)` — returns a short status string for a tool

## Session isolation

`ProgressManager` fans out ALL events to ALL registered notifiers. Each notifier is responsible for filtering by `turnId` — only acting on sessions it has registered. Unknown `turnId` values are ignored.
```

- [ ] **Step 5: Run full test suite and build**

```bash
npm run build && npm run test
```

Expected: No errors, all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/progress/notifiers/slack.ts src/progress/notifiers/websocket.ts src/progress/README.md src/progress/index.ts
git commit -m "feat(progress): stub notifiers for Slack and WebSocket + README contract"
```
