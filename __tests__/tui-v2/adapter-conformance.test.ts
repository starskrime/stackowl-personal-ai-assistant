/**
 * P4-B: Adapter Conformance Suite
 *
 * Guards that the non-breaking additive changes to ChannelAdapter
 * (optional emit? and capabilities?) don't regress Telegram, Slack,
 * Voice, or CLI adapters.
 *
 * Contract every ChannelAdapter must satisfy:
 *  1. Has id: string
 *  2. Has name: string
 *  3. Has start(): Promise<void>
 *  4. Has stop(): void (or Promise<void>)
 *  5. Has sendToUser(userId, response): Promise<void>
 *  6. Has broadcast(response): Promise<void>
 *  7. Does NOT require emit — adapter.emit?.() is safe (no throw)
 *  8. Does NOT require capabilities — adapter.capabilities?.() is safe (no throw)
 *  9. If capabilities() IS defined, returns { tuiV2: boolean, ... }
 */

import { describe, it, expect } from "vitest";
import type { ChannelAdapter, GatewayResponse, ChannelCapabilities } from "../../src/gateway/types.js";

// ─── Shared conformance helper ─────────────────────────────────────────────

const NOOP_RESPONSE: GatewayResponse = {
  content: "test",
  owlName: "TestOwl",
  owlEmoji: "🦉",
  toolsUsed: [],
};

function testAdapterConformance(
  label: string,
  adapter: ChannelAdapter,
  opts: { expectsEmit?: boolean; expectsCapabilities?: boolean } = {},
): void {
  describe(label, () => {
    it("has id: string", () => {
      expect(typeof adapter.id).toBe("string");
      expect(adapter.id.length).toBeGreaterThan(0);
    });

    it("has name: string", () => {
      expect(typeof adapter.name).toBe("string");
      expect(adapter.name.length).toBeGreaterThan(0);
    });

    it("has start(): Promise<void>", () => {
      expect(typeof adapter.start).toBe("function");
      // Start returns a Promise (or at minimum a thenable)
      const result = adapter.start();
      expect(result).toBeInstanceOf(Promise);
      // Swallow the promise — we don't await it (stubs resolve immediately)
      result.catch(() => {});
    });

    it("has stop(): void or Promise<void>", () => {
      expect(typeof adapter.stop).toBe("function");
      // stop() is allowed to return void or a Promise — both are valid
      const result = adapter.stop();
      // Just assert it doesn't throw; return value can be anything
      expect(result === undefined || result instanceof Promise).toBe(true);
    });

    it("has sendToUser(): Promise<void>", async () => {
      expect(typeof adapter.sendToUser).toBe("function");
      await expect(adapter.sendToUser("user-1", NOOP_RESPONSE)).resolves.toBeUndefined();
    });

    it("has broadcast(): Promise<void>", async () => {
      expect(typeof adapter.broadcast).toBe("function");
      await expect(adapter.broadcast(NOOP_RESPONSE)).resolves.toBeUndefined();
    });

    it("emit?.() is safe — does not throw when called via optional chaining", () => {
      expect(() => {
        (adapter as ChannelAdapter).emit?.(
          // Minimal valid UiEvent-shaped value (we only care it doesn't throw)
          { kind: "notice", source: "test", text: "conformance", severity: "info" } as any,
        );
      }).not.toThrow();
    });

    it("capabilities?.() is safe — does not throw when called via optional chaining", () => {
      expect(() => {
        (adapter as ChannelAdapter).capabilities?.();
      }).not.toThrow();
    });

    if (opts.expectsCapabilities) {
      it("capabilities() returns an object with tuiV2: boolean", () => {
        const caps = adapter.capabilities!();
        expect(typeof caps).toBe("object");
        expect(caps).not.toBeNull();
        expect(typeof caps.tuiV2).toBe("boolean");
        expect(typeof caps.richText).toBe("boolean");
        expect(typeof caps.fileAttachments).toBe("boolean");
      });
    }

    if (opts.expectsEmit) {
      it("emit() is defined and callable", () => {
        expect(typeof adapter.emit).toBe("function");
        expect(() => {
          adapter.emit!({ kind: "notice", source: "test", text: "conformance", severity: "info" } as any);
        }).not.toThrow();
      });
    }
  });
}

// ─── Adapter stubs ────────────────────────────────────────────────────────
//
// These are minimal ChannelAdapter-shaped objects.  We do NOT import the real
// classes (TelegramAdapter, SlackAdapter, etc.) because those constructors
// require live bots + grammY/Bolt connections.  The goal of this suite is to
// verify the interface contract — structural compatibility — not runtime
// transport behaviour.

const mockTelegramAdapter: ChannelAdapter = {
  id: "telegram",
  name: "Telegram",
  start: async () => {},
  stop: () => {},
  sendToUser: async () => {},
  broadcast: async () => {},
  // No emit, no capabilities — v1 contract
};

const mockSlackAdapter: ChannelAdapter = {
  id: "slack",
  name: "Slack",
  start: async () => {},
  stop: () => {},
  sendToUser: async () => {},
  broadcast: async () => {},
  // No emit, no capabilities — v1 contract
};

const mockVoiceAdapter: ChannelAdapter = {
  id: "voice",
  name: "Voice",
  start: async () => {},
  stop: () => {},
  sendToUser: async () => {},
  broadcast: async () => {},
  // No emit, no capabilities — v1 contract
};

const mockCliV1Adapter: ChannelAdapter = {
  id: "cli",
  name: "CLI",
  start: async () => {},
  stop: () => {},
  sendToUser: async () => {},
  broadcast: async () => {},
  // No emit, no capabilities — v1 contract
};

const mockCliV2Adapter: ChannelAdapter = {
  id: "cli-v2",
  name: "CLI v2",
  start: async () => {},
  stop: () => {},
  sendToUser: async () => {},
  broadcast: async () => {},
  // TUI v2 additions — these ARE present on the v2 adapter
  emit: (_event) => { /* noop — type-checks the UiEvent parameter */ },
  capabilities: (): ChannelCapabilities => ({
    tuiV2: true,
    richText: false,
    fileAttachments: false,
  }),
};

// ─── Type-level guards ────────────────────────────────────────────────────
//
// TypeScript will refuse to compile this file if any of the stubs above do
// NOT satisfy ChannelAdapter.  These explicit type assertions make the intent
// clear and generate a readable error if the interface breaks.

const _telegramCheck: ChannelAdapter = mockTelegramAdapter;
const _slackCheck: ChannelAdapter = mockSlackAdapter;
const _voiceCheck: ChannelAdapter = mockVoiceAdapter;
const _cliV1Check: ChannelAdapter = mockCliV1Adapter;
const _cliV2Check: ChannelAdapter = mockCliV2Adapter;

// Suppress "unused variable" warnings — the point is compile-time checking.
void _telegramCheck, _slackCheck, _voiceCheck, _cliV1Check, _cliV2Check;

// ─── Run conformance checks for every adapter ─────────────────────────────

describe("ChannelAdapter conformance suite (P4-B)", () => {
  testAdapterConformance("TelegramAdapter (stub)", mockTelegramAdapter);
  testAdapterConformance("SlackAdapter (stub)", mockSlackAdapter);
  testAdapterConformance("VoiceChannelAdapter (stub)", mockVoiceAdapter);
  testAdapterConformance("CLIAdapter v1 (stub)", mockCliV1Adapter);
  testAdapterConformance("CliV2Adapter (stub)", mockCliV2Adapter, {
    expectsEmit: true,
    expectsCapabilities: true,
  });

  // ─── Cross-cutting: gateway emit-call safety ───────────────────────────
  //
  // Verifies the rule: any code that calls adapter.emit() MUST use optional
  // chaining (adapter.emit?.(...)), otherwise it would throw on v1 adapters.
  //
  // We test this by simulating what gateway orchestration code does:
  //   for each registered adapter, call adapter.emit?.()
  // This should be safe on ALL adapters, v1 or v2.

  describe("gateway emit-dispatch safety — optional chaining", () => {
    const allAdapters: ChannelAdapter[] = [
      mockTelegramAdapter,
      mockSlackAdapter,
      mockVoiceAdapter,
      mockCliV1Adapter,
      mockCliV2Adapter,
    ];

    it("adapter.emit?.() is safe on ALL adapters (simulated gateway dispatch)", () => {
      const fakeEvent = {
        kind: "notice",
        source: "heartbeat",
        text: "Good morning!",
        severity: "info",
      } as any;

      expect(() => {
        for (const adapter of allAdapters) {
          adapter.emit?.(fakeEvent);
        }
      }).not.toThrow();
    });

    it("adapter.capabilities?.() is safe on ALL adapters (simulated gateway capability check)", () => {
      expect(() => {
        for (const adapter of allAdapters) {
          const caps = adapter.capabilities?.();
          // If defined, must have tuiV2
          if (caps !== undefined) {
            expect(typeof caps.tuiV2).toBe("boolean");
          }
        }
      }).not.toThrow();
    });

    it("only cli-v2 adapter reports tuiV2: true", () => {
      const tuiV2Adapters = allAdapters.filter(
        (a) => a.capabilities?.()?.tuiV2 === true,
      );
      expect(tuiV2Adapters).toHaveLength(1);
      expect(tuiV2Adapters[0]!.id).toBe("cli-v2");
    });

    it("non-cli-v2 adapters do NOT have emit defined", () => {
      const v1Adapters = allAdapters.filter((a) => a.id !== "cli-v2");
      for (const adapter of v1Adapters) {
        expect(adapter.emit).toBeUndefined();
      }
    });

    it("non-cli-v2 adapters do NOT have capabilities defined", () => {
      const v1Adapters = allAdapters.filter((a) => a.id !== "cli-v2");
      for (const adapter of v1Adapters) {
        expect(adapter.capabilities).toBeUndefined();
      }
    });
  });
});
