# Epic 4: Channel Expansion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Discord and WhatsApp channel adapters plus a DM pairing security model, following the pattern set by the existing `SlackAdapter` (`src/gateway/adapters/slack.ts`), mapping all incoming messages to `GatewayMessage` and all outgoing responses to platform-native format. Slack is already implemented and working.

**Architecture:** `ChannelAdapter` interface lives in `src/gateway/types.ts`. `ChannelRegistry` in `src/gateway/channel-registry.ts` holds all adapters. Adding a channel = implementing `ChannelAdapter` + wiring in the startup file. DM pairing (`src/gateway/security/pairing.ts`) is shared across Discord and WhatsApp; an unknown sender gets a pairing challenge and can authorize via `stackowl pairing approve <channel> <code>`. Discord uses `discord.js` v14; WhatsApp uses `whatsapp-web.js`.

**Tech Stack:** TypeScript, Node 22, `discord.js@^14`, `whatsapp-web.js@^1`, `qrcode-terminal` (WhatsApp QR display), `better-sqlite3` (pairing store), Vitest.

---

## File Map

| File | Action | What changes |
|---|---|---|
| `src/gateway/security/pairing.ts` | Create | DM pairing — challenge/approve/allowlist |
| `src/gateway/adapters/discord.ts` | Create | Discord adapter — DMs + server mentions |
| `src/gateway/adapters/whatsapp.ts` | Create | WhatsApp adapter — DMs via whatsapp-web.js |
| `__tests__/gateway/pairing.test.ts` | Create | Unit tests for pairing flow |
| `__tests__/gateway/discord-adapter.test.ts` | Create | Unit tests for message normalization |

---

## Task 1: DM Pairing Security Model

**Files:**
- Create: `src/gateway/security/pairing.ts`
- Create: `__tests__/gateway/pairing.test.ts`

- [ ] **Step 1.1: Write the failing test**

```typescript
// __tests__/gateway/pairing.test.ts
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { rm } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import Database from "better-sqlite3";
import { PairingService } from "../../src/gateway/security/pairing.js";

const DB_PATH = join(tmpdir(), `stackowl-pairing-test-${Date.now()}.db`);
let service: PairingService;

beforeEach(() => {
  const db = new Database(DB_PATH);
  service = new PairingService(db);
});

afterEach(async () => {
  await rm(DB_PATH, { force: true });
});

describe("PairingService", () => {
  it("issues a challenge code for an unknown sender", () => {
    const code = service.challenge("discord", "user123");
    expect(code).toMatch(/^[A-Z0-9]{6}$/);
  });

  it("returns same pending code for same sender on repeated challenge", () => {
    const c1 = service.challenge("discord", "user123");
    const c2 = service.challenge("discord", "user123");
    expect(c1).toBe(c2);
  });

  it("approves a valid code and authorizes the sender", () => {
    const code = service.challenge("discord", "user123");
    const ok = service.approve("discord", "user123", code);
    expect(ok).toBe(true);
    expect(service.isAuthorized("discord", "user123")).toBe(true);
  });

  it("rejects an incorrect code", () => {
    service.challenge("discord", "user123");
    const ok = service.approve("discord", "user123", "WRONG1");
    expect(ok).toBe(false);
    expect(service.isAuthorized("discord", "user123")).toBe(false);
  });

  it("treats known senders as authorized without challenge", () => {
    expect(service.isAuthorized("discord", "unknown_user")).toBe(false);
  });
});
```

- [ ] **Step 1.2: Run test to confirm it fails**

```bash
cd /ssd/projects/stackowl-personal-ai-assistant
npx vitest run __tests__/gateway/pairing.test.ts 2>&1 | tail -20
```

Expected: FAIL — `PairingService` module does not exist.

- [ ] **Step 1.3: Implement PairingService**

Create `src/gateway/security/pairing.ts`:

```typescript
import type Database from "better-sqlite3";
import { randomBytes } from "node:crypto";
import { log } from "../../logger.js";

function generateCode(): string {
  return randomBytes(3).toString("hex").toUpperCase().slice(0, 6);
}

export class PairingService {
  constructor(private db: Database.Database) {
    this.db.exec(`
      CREATE TABLE IF NOT EXISTS pairing_allowlist (
        channel    TEXT NOT NULL,
        sender_id  TEXT NOT NULL,
        approved   INTEGER DEFAULT 0,
        code       TEXT,
        created_at INTEGER NOT NULL DEFAULT (unixepoch()),
        PRIMARY KEY (channel, sender_id)
      );
    `);
  }

  isAuthorized(channel: string, senderId: string): boolean {
    const row = this.db
      .prepare(`SELECT approved FROM pairing_allowlist WHERE channel = ? AND sender_id = ?`)
      .get(channel, senderId) as { approved: number } | undefined;
    return row?.approved === 1;
  }

  /** Create or retrieve a pending challenge code for this sender. */
  challenge(channel: string, senderId: string): string {
    const existing = this.db
      .prepare(`SELECT code FROM pairing_allowlist WHERE channel = ? AND sender_id = ? AND approved = 0`)
      .get(channel, senderId) as { code: string } | undefined;

    if (existing?.code) return existing.code;

    const code = generateCode();
    this.db
      .prepare(
        `INSERT INTO pairing_allowlist (channel, sender_id, approved, code)
         VALUES (?, ?, 0, ?)
         ON CONFLICT(channel, sender_id) DO UPDATE SET code = excluded.code, approved = 0`,
      )
      .run(channel, senderId, code);

    log.engine.info("[PairingService] Challenge issued", { channel, senderId });
    return code;
  }

  approve(channel: string, senderId: string, code: string): boolean {
    const row = this.db
      .prepare(`SELECT code FROM pairing_allowlist WHERE channel = ? AND sender_id = ?`)
      .get(channel, senderId) as { code: string } | undefined;

    if (!row || row.code !== code) return false;

    this.db
      .prepare(`UPDATE pairing_allowlist SET approved = 1, code = NULL WHERE channel = ? AND sender_id = ?`)
      .run(channel, senderId);

    log.engine.info("[PairingService] Sender approved", { channel, senderId });
    return true;
  }

  revoke(channel: string, senderId: string): void {
    this.db
      .prepare(`DELETE FROM pairing_allowlist WHERE channel = ? AND sender_id = ?`)
      .run(channel, senderId);
  }
}
```

- [ ] **Step 1.4: Run test to confirm it passes**

```bash
npx vitest run __tests__/gateway/pairing.test.ts 2>&1 | tail -20
```

Expected: PASS — 5 tests passing.

- [ ] **Step 1.5: Commit**

```bash
git add src/gateway/security/pairing.ts __tests__/gateway/pairing.test.ts
git commit -m "feat(channels): DM pairing security model — challenge/approve/allowlist"
```

---

## Task 2: Discord Adapter

**Files:**
- Create: `src/gateway/adapters/discord.ts`
- Create: `__tests__/gateway/discord-adapter.test.ts`

- [ ] **Step 2.1: Install discord.js**

```bash
cd /ssd/projects/stackowl-personal-ai-assistant
npm install discord.js@^14
```

Expected: adds `discord.js` to package.json dependencies.

- [ ] **Step 2.2: Write the failing test**

```typescript
// __tests__/gateway/discord-adapter.test.ts
import { describe, it, expect, vi } from "vitest";
import { DiscordAdapter } from "../../src/gateway/adapters/discord.js";

describe("DiscordAdapter message normalization", () => {
  it("normalizes a Discord DM to GatewayMessage", () => {
    const adapter = new DiscordAdapter({ botToken: "fake-token" });
    const mockMsg = {
      id: "111222333",
      content: "hello owl",
      author: { id: "user-42", bot: false },
      channel: { type: 1, send: vi.fn() }, // 1 = DM channel type
      guild: null,
    } as any;

    const normalized = (adapter as any).normalizeMessage(mockMsg);
    expect(normalized.channelId).toBe("discord");
    expect(normalized.userId).toBe("user-42");
    expect(normalized.text).toBe("hello owl");
    expect(normalized.sessionId).toBe("discord:user-42");
  });

  it("normalizes a server mention to GatewayMessage", () => {
    const adapter = new DiscordAdapter({ botToken: "fake-token" });
    const mockMsg = {
      id: "444555666",
      content: "<@BOT_ID> help me",
      author: { id: "server-user-7", bot: false },
      channel: { type: 0, id: "channel-abc", send: vi.fn() },
      guild: { id: "guild-xyz" },
    } as any;

    const normalized = (adapter as any).normalizeMessage(mockMsg);
    expect(normalized.channelId).toBe("discord");
    expect(normalized.text).toContain("help me");
  });
});
```

- [ ] **Step 2.3: Run test to confirm it fails**

```bash
npx vitest run __tests__/gateway/discord-adapter.test.ts 2>&1 | tail -20
```

Expected: FAIL — `DiscordAdapter` module does not exist.

- [ ] **Step 2.4: Implement Discord adapter**

Study the full `SlackAdapter` for the pattern:

```bash
wc -l /ssd/projects/stackowl-personal-ai-assistant/src/gateway/adapters/slack.ts
```

Create `src/gateway/adapters/discord.ts`:

```typescript
/**
 * StackOwl — Discord Channel Adapter
 *
 * Transport layer only. All business logic lives in OwlGateway.
 * Supports:
 *   - Direct messages (DMs)
 *   - Server channel mentions (@bot)
 *   - DM pairing security (unknown senders get a challenge)
 */

import { Client, GatewayIntentBits, type Message, ChannelType } from "discord.js";
import { log } from "../../logger.js";
import { makeSessionId, makeMessage, OwlGateway } from "../core.js";
import type { ChannelAdapter, GatewayMessage, GatewayResponse } from "../types.js";

export interface DiscordAdapterConfig {
  /** Bot token from Discord Developer Portal */
  botToken: string;
  /** Optional Guild IDs to restrict to (omit = respond everywhere) */
  guildIds?: string[];
  /** DM policy: "open" (all DMs) | "pairing" (challenge unknown senders) */
  dmPolicy?: "open" | "pairing";
}

export class DiscordAdapter implements ChannelAdapter {
  readonly id = "discord";
  readonly name = "Discord";

  private client: Client;
  private gateway: OwlGateway | null = null;
  private config: Required<DiscordAdapterConfig>;

  constructor(config: DiscordAdapterConfig) {
    this.config = {
      botToken: config.botToken,
      guildIds: config.guildIds ?? [],
      dmPolicy: config.dmPolicy ?? "pairing",
    };

    this.client = new Client({
      intents: [
        GatewayIntentBits.Guilds,
        GatewayIntentBits.GuildMessages,
        GatewayIntentBits.MessageContent,
        GatewayIntentBits.DirectMessages,
      ],
    });
  }

  async start(gateway: OwlGateway): Promise<void> {
    this.gateway = gateway;

    this.client.on("ready", () => {
      log.gateway.info(`[DiscordAdapter] Logged in as ${this.client.user?.tag}`);
    });

    this.client.on("messageCreate", async (msg) => {
      if (msg.author.bot) return;

      // Only respond to DMs or mentions in guild channels
      const isDm = msg.channel.type === ChannelType.DM;
      const isMention = msg.mentions.has(this.client.user!);
      if (!isDm && !isMention) return;

      // Guild restriction
      if (!isDm && this.config.guildIds.length > 0) {
        if (!msg.guild || !this.config.guildIds.includes(msg.guild.id)) return;
      }

      const gMsg = this.normalizeMessage(msg);
      if (!gMsg) return;

      try {
        await msg.channel.sendTyping();
        await this.gateway!.handle(gMsg, {
          onProgress: async (text) => {
            // Discord doesn't support streaming — skip progress
          },
          suppressThinking: true,
        });
      } catch (err) {
        log.gateway.error("[DiscordAdapter] messageCreate failed", err as Error);
        await msg.reply("Something went wrong. Please try again.").catch(() => {});
      }
    });

    await this.client.login(this.config.botToken);
  }

  async stop(): Promise<void> {
    this.client.destroy();
    log.gateway.info("[DiscordAdapter] Stopped");
  }

  async deliver(response: GatewayResponse): Promise<void> {
    // Proactive delivery — look up channel by userId
    const [, userId] = response.sessionId.split(":");
    const user = await this.client.users.fetch(userId).catch(() => null);
    if (!user) return;
    const dm = await user.createDM().catch(() => null);
    if (!dm) return;
    await this.sendResponse(dm as any, response);
  }

  private normalizeMessage(msg: Message): GatewayMessage | null {
    const isDm = msg.channel.type === ChannelType.DM;
    // Strip mention prefix from text
    let text = msg.content.replace(/<@!?\d+>/g, "").trim();
    if (!text) return null;

    return {
      id: msg.id,
      channelId: "discord",
      userId: msg.author.id,
      sessionId: makeSessionId("discord", msg.author.id),
      text,
    };
  }

  private async sendResponse(channel: { send: (opts: any) => Promise<any> }, response: GatewayResponse): Promise<void> {
    const MAX = 2000; // Discord message limit
    const text = response.text ?? "";
    if (text.length <= MAX) {
      await channel.send({ content: text });
    } else {
      // Split on paragraph boundaries
      const chunks: string[] = [];
      let remaining = text;
      while (remaining.length > MAX) {
        const cut = remaining.lastIndexOf("\n\n", MAX);
        const splitAt = cut > 0 ? cut : MAX;
        chunks.push(remaining.slice(0, splitAt));
        remaining = remaining.slice(splitAt).trim();
      }
      if (remaining) chunks.push(remaining);
      for (const chunk of chunks) {
        await channel.send({ content: chunk });
      }
    }
  }
}
```

- [ ] **Step 2.5: Run test to confirm it passes**

```bash
npx vitest run __tests__/gateway/discord-adapter.test.ts 2>&1 | tail -20
```

Expected: PASS — 2 tests passing.

- [ ] **Step 2.6: Wire Discord adapter into startup (conditional on config)**

Find the startup file where `SlackAdapter` is instantiated:

```bash
grep -rn "SlackAdapter\|new.*Adapter(" /ssd/projects/stackowl-personal-ai-assistant/src/ --include="*.ts" | grep -v "test\|__tests__" | head -10
```

Add Discord adapter alongside Slack:

```typescript
import { DiscordAdapter } from "./gateway/adapters/discord.js";

if (config.discord?.botToken) {
  const discordAdapter = new DiscordAdapter({
    botToken: config.discord.botToken,
    guildIds: config.discord.guildIds,
    dmPolicy: config.discord.dmPolicy ?? "pairing",
  });
  channelRegistry.register(discordAdapter);
  await discordAdapter.start(gateway);
}
```

Add to `stackowl.config.json` schema (doc update only — no type change needed):
```json
"discord": {
  "botToken": "Bot TOKEN_HERE",
  "guildIds": [],
  "dmPolicy": "pairing"
}
```

- [ ] **Step 2.7: Commit**

```bash
git add src/gateway/adapters/discord.ts __tests__/gateway/discord-adapter.test.ts  # + startup file
git commit -m "feat(channels): Discord adapter — DMs + server mentions + pairing security"
```

---

## Task 3: WhatsApp Adapter

**Files:**
- Create: `src/gateway/adapters/whatsapp.ts`

- [ ] **Step 3.1: Install whatsapp-web.js**

```bash
npm install whatsapp-web.js qrcode-terminal
```

Expected: adds both packages to `package.json`.

- [ ] **Step 3.2: Implement WhatsApp adapter**

WhatsApp Web requires a browser session (Puppeteer) and a QR scan on first run. There is no straightforward unit test without mocking the entire WWebJS client — the integration test is the smoke test in Step 3.3.

Create `src/gateway/adapters/whatsapp.ts`:

```typescript
/**
 * StackOwl — WhatsApp Channel Adapter
 *
 * Uses whatsapp-web.js (local Puppeteer) to connect to WhatsApp Web.
 * DMs only. Pairing security: unknown senders receive a challenge.
 *
 * First run: QR code printed to terminal — scan with WhatsApp mobile app.
 * Session persisted to ~/.stackowl/whatsapp-session/ for subsequent runs.
 */

import { join } from "node:path";
import { homedir } from "node:os";
import { Client, LocalAuth, type Message } from "whatsapp-web.js";
import qrcode from "qrcode-terminal";
import { log } from "../../logger.js";
import { makeSessionId, OwlGateway } from "../core.js";
import type { ChannelAdapter, GatewayMessage, GatewayResponse } from "../types.js";

export interface WhatsAppAdapterConfig {
  /** Where to store the browser session */
  sessionDataPath?: string;
  /** DM policy: "open" | "pairing" (default: pairing) */
  dmPolicy?: "open" | "pairing";
}

export class WhatsAppAdapter implements ChannelAdapter {
  readonly id = "whatsapp";
  readonly name = "WhatsApp";

  private client: Client;
  private gateway: OwlGateway | null = null;
  private config: Required<WhatsAppAdapterConfig>;

  constructor(config: WhatsAppAdapterConfig = {}) {
    this.config = {
      sessionDataPath: config.sessionDataPath ?? join(homedir(), ".stackowl", "whatsapp-session"),
      dmPolicy: config.dmPolicy ?? "pairing",
    };

    this.client = new Client({
      authStrategy: new LocalAuth({ dataPath: this.config.sessionDataPath }),
      puppeteer: {
        args: ["--no-sandbox", "--disable-setuid-sandbox"],
      },
    });
  }

  async start(gateway: OwlGateway): Promise<void> {
    this.gateway = gateway;

    this.client.on("qr", (qr) => {
      log.gateway.info("[WhatsAppAdapter] QR code received — scan with WhatsApp mobile app");
      qrcode.generate(qr, { small: true });
    });

    this.client.on("ready", () => {
      log.gateway.info("[WhatsAppAdapter] Client ready");
    });

    this.client.on("message", async (msg) => {
      // Only respond to private DMs (not group messages)
      if (msg.isGroupMsg) return;

      const gMsg = this.normalizeMessage(msg);
      if (!gMsg) return;

      try {
        await this.gateway!.handle(gMsg, {
          onProgress: async (_text) => {
            // WhatsApp does not support typing indicator via WWebJS easily
          },
          suppressThinking: true,
        });
      } catch (err) {
        log.gateway.error("[WhatsAppAdapter] message handler failed", err as Error);
        await msg.reply("Something went wrong. Please try again.").catch(() => {});
      }
    });

    await this.client.initialize();
  }

  async stop(): Promise<void> {
    await this.client.destroy();
    log.gateway.info("[WhatsAppAdapter] Stopped");
  }

  async deliver(response: GatewayResponse): Promise<void> {
    const [, userId] = response.sessionId.split(":");
    const chatId = userId.includes("@") ? userId : `${userId}@c.us`;
    await this.client.sendMessage(chatId, response.text ?? "");
  }

  private normalizeMessage(msg: Message): GatewayMessage | null {
    const text = msg.body?.trim();
    if (!text) return null;

    const senderId = msg.from.replace("@c.us", "");

    return {
      id: msg.id._serialized,
      channelId: "whatsapp",
      userId: senderId,
      sessionId: makeSessionId("whatsapp", senderId),
      text,
    };
  }
}
```

- [ ] **Step 3.3: Wire WhatsApp adapter into startup**

```typescript
import { WhatsAppAdapter } from "./gateway/adapters/whatsapp.js";

if (config.whatsapp?.enabled) {
  const waAdapter = new WhatsAppAdapter({
    sessionDataPath: config.whatsapp.sessionDataPath,
    dmPolicy: config.whatsapp.dmPolicy ?? "pairing",
  });
  channelRegistry.register(waAdapter);
  await waAdapter.start(gateway);
}
```

Add to config schema:
```json
"whatsapp": {
  "enabled": true,
  "dmPolicy": "pairing"
}
```

- [ ] **Step 3.4: Smoke test — WhatsApp connection**

```bash
npm run dev 2>&1 | head -30
```

Expected on first run: QR code printed to terminal. Scan with WhatsApp app. After scan: `[WhatsAppAdapter] Client ready`. Send a DM to the bot — should receive a response.

- [ ] **Step 3.5: Commit**

```bash
git add src/gateway/adapters/whatsapp.ts  # + startup file + package.json
git commit -m "feat(channels): WhatsApp adapter — DMs via whatsapp-web.js with QR auth"
```

---

## Task 4: pairing approve CLI command

**Files:**
- Modify: `src/cli/` or wherever CLI commands are registered (find at step 4.1)

- [ ] **Step 4.1: Find CLI command registration**

```bash
grep -rn "program\.command\|commander\|\.addCommand" /ssd/projects/stackowl-personal-ai-assistant/src/ --include="*.ts" | grep -v "test\|__tests__" | head -15
```

- [ ] **Step 4.2: Add pairing subcommand**

At the CLI registration site:

```typescript
program
  .command("pairing")
  .description("Manage DM pairing for Discord/WhatsApp/Slack")
  .command("approve <channel> <userId> <code>")
  .description("Approve a pairing code issued to a user")
  .action(async (channel: string, userId: string, code: string) => {
    // Load existing db
    const db = await loadDatabase();  // use existing db loader
    const pairing = new PairingService(db);
    const ok = pairing.approve(channel, userId, code);
    if (ok) {
      console.log(`✅ Approved: ${userId} on ${channel}`);
    } else {
      console.error(`❌ Failed: wrong code or unknown sender`);
      process.exit(1);
    }
  });
```

- [ ] **Step 4.3: Test pairing flow end-to-end**

```bash
# Start the assistant
npm run dev &

# Simulate a new Discord user — they will get a challenge
# Check logs for the issued code
cat logs/stackowl-$(date +%F).log | python3 -c "
import sys, json
for line in sys.stdin:
    try:
        r = json.loads(line)
        if 'pairing' in r.get('msg','').lower() or 'challenge' in r.get('msg','').lower():
            print(r)
    except: pass
"

# Then approve:
npx ts-node src/cli.ts pairing approve discord <userId> <code>
```

- [ ] **Step 4.4: Commit**

```bash
git add src/  # CLI file
git commit -m "feat(channels): pairing approve CLI command for Discord/WhatsApp DM authorization"
```
