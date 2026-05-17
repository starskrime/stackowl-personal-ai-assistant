import { describe, it, expect, vi, beforeEach } from "vitest";
import { TelegramCommandRouter } from "../../../src/gateway/adapters/telegram/command-router.js";
import { TelegramCallbackRouter } from "../../../src/gateway/adapters/telegram/callback-router.js";
import type { CommandSpec } from "../../../src/cli/v2/commands/registry.js";
import type { ChannelCommandRouter } from "../../../src/gateway/adapters/channel-command-router.js";

const makeSpec = (name: string, opts: Partial<CommandSpec> = {}): CommandSpec => ({
  name,
  description: `${name} description`,
  handler: async () => ({ kind: "action" }),
  ...opts,
});

const makeMockBot = () => {
  const registered: string[] = [];
  return {
    command: vi.fn((cmd: string | string[], _handler: unknown) => {
      const names = Array.isArray(cmd) ? cmd : [cmd];
      registered.push(...names);
    }),
    _registered: registered,
    use: vi.fn(),
    api: { setMyCommands: vi.fn().mockResolvedValue(true) },
  };
};

const makeMockGateway = () => ({
  handle: vi.fn().mockResolvedValue({ content: "ok", owlEmoji: "🦉", owlName: "Owl" }),
  getOwl: vi.fn().mockReturnValue({ persona: { emoji: "🦉", name: "Test" } }),
  getConfig: vi.fn().mockReturnValue({}),
  endSession: vi.fn().mockResolvedValue(undefined),
});

describe("TelegramCommandRouter", () => {
  let bot: ReturnType<typeof makeMockBot>;
  let gateway: ReturnType<typeof makeMockGateway>;
  let registry: CommandSpec[];

  beforeEach(() => {
    bot = makeMockBot();
    gateway = makeMockGateway();
    registry = [
      makeSpec("/help"),
      makeSpec("/status"),
      makeSpec("/mcp"),
      makeSpec("/config", { telegramSpecialCase: true }),
      makeSpec("/quit",   { telegramVisible: false }),
    ];
  });

  it("register() registers non-special commands on bot", () => {
    const router = new TelegramCommandRouter({ gateway: gateway as any, registry, specialCaseHandlers: {} });
    router.register(bot as any);
    expect(bot.command).toHaveBeenCalledWith(expect.anything(), expect.any(Function));
    // /help, /status, /mcp registered; /config (special case) skipped in loop
    const allRegistered = bot._registered.flat();
    expect(allRegistered).toContain("help");
    expect(allRegistered).toContain("status");
  });

  it("register() skips telegramSpecialCase commands in the loop", () => {
    const router = new TelegramCommandRouter({ gateway: gateway as any, registry, specialCaseHandlers: {} });
    router.register(bot as any);
    expect(bot._registered).not.toContain("config");
  });

  it("updateBotMenu() calls setMyCommands with only visible non-special commands", async () => {
    const router = new TelegramCommandRouter({ gateway: gateway as any, registry, specialCaseHandlers: {} });
    await router.updateBotMenu(bot as any);
    expect(bot.api.setMyCommands).toHaveBeenCalledOnce();
    const commands = bot.api.setMyCommands.mock.calls[0][0] as Array<{ command: string }>;
    const names = commands.map(c => c.command);
    expect(names).toContain("help");
    expect(names).toContain("status");
    expect(names).not.toContain("quit");
    expect(names).not.toContain("config");
  });

  it("updateBotMenu() truncates descriptions over 253 chars", async () => {
    const longDesc = makeSpec("/long", { description: "x".repeat(300) });
    const router = new TelegramCommandRouter({ gateway: gateway as any, registry: [longDesc], specialCaseHandlers: {} });
    await router.updateBotMenu(bot as any);
    const commands = bot.api.setMyCommands.mock.calls[0][0] as Array<{ description: string }>;
    const hasLong = commands.find(c => c.command === "long");
    if (hasLong) {
      expect(hasLong.description.length).toBeLessThanOrEqual(256);
      expect(hasLong.description.endsWith("...")).toBe(true);
    }
  });

  it("updateBotMenu() does not throw on setMyCommands failure", async () => {
    bot.api.setMyCommands.mockRejectedValue(new Error("API down"));
    const router = new TelegramCommandRouter({ gateway: gateway as any, registry, specialCaseHandlers: {} });
    await expect(router.updateBotMenu(bot as any)).resolves.not.toThrow();
  });
});

describe("TelegramCallbackRouter", () => {
  it("constructs without error", () => {
    const router = new TelegramCallbackRouter({
      isAllowed: () => true,
      handlers: {
        onNav:      vi.fn(),
        onWizard:   vi.fn(),
        onConfig:   vi.fn(),
        onVoice:    vi.fn(),
        onFeedback: vi.fn(),
      },
    });
    expect(router).toBeDefined();
  });
});

// ── TelegramTextHandler + TelegramVoiceHandler ────────────────────────────────
import { TelegramTextHandler } from "../../../src/gateway/adapters/telegram/text-handler.js";
import { TelegramVoiceHandler } from "../../../src/gateway/adapters/telegram/voice-handler.js";

const makeGateway = () => ({
  handle: vi.fn().mockResolvedValue({ content: "ok", owlEmoji: "🦉", owlName: "Owl" }),
  getOwl: vi.fn().mockReturnValue({ persona: { emoji: "🦉", name: "Test" } }),
  getConfig: vi.fn().mockReturnValue({}),
  endSession: vi.fn().mockResolvedValue(undefined),
  getCognitiveLoop: vi.fn().mockReturnValue(null),
});

describe("TelegramTextHandler", () => {
  it("constructs without error", () => {
    const handler = new TelegramTextHandler({
      gateway: makeGateway() as any,
      isAllowed: () => true,
      trackChat: vi.fn(),
      sessionStore: { get: vi.fn(), set: vi.fn(), has: vi.fn(), delete: vi.fn(), destroy: vi.fn() } as any,
      pinger: null,
    });
    expect(handler).toBeDefined();
  });
});

describe("TelegramVoiceHandler", () => {
  it("constructs without error", () => {
    const handler = new TelegramVoiceHandler({
      gateway: makeGateway() as any,
      isAllowed: () => true,
      trackChat: vi.fn(),
      stt: { transcribe: vi.fn() } as any,
      botToken: "test-token",
    });
    expect(handler).toBeDefined();
  });
});

describe("ChannelCommandRouter interface", () => {
  it("TelegramCommandRouter satisfies ChannelCommandRouter", () => {
    // Type-level test: if this compiles, the interface is satisfied at runtime too
    const router: ChannelCommandRouter = new TelegramCommandRouter({
      gateway: makeGateway() as any,
      registry: [],
      specialCaseHandlers: {},
    });
    expect(typeof router.register).toBe("function");
    expect(typeof router.updateMenu).toBe("function");
  });
});
