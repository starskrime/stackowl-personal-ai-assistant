import { describe, it, expect, vi } from "vitest";
import { TelegramMessageProcessor } from "../../../src/gateway/adapters/telegram/message-processor.js";

const makeGateway = () => ({
  handle: vi.fn().mockResolvedValue({ content: "response", owlEmoji: "🦉", owlName: "Owl" }),
  getOwl: vi.fn().mockReturnValue({ persona: { emoji: "🦉", name: "Owl" } }),
  getConfig: vi.fn().mockReturnValue({ gateway: { suppressThinkingMessages: true } }),
  getCognitiveLoop: vi.fn().mockReturnValue(null),
});

const makeCtx = () => ({
  chat: { id: 123 },
  from: { id: 456 },
  reply: vi.fn().mockResolvedValue({ message_id: 1 }),
  api: {
    sendMessage: vi.fn().mockResolvedValue({ message_id: 1 }),
    editMessageText: vi.fn().mockResolvedValue(true),
  },
});

describe("TelegramMessageProcessor", () => {
  it("calls gateway.handle() with the message", async () => {
    const gateway = makeGateway();
    const ctx = makeCtx();
    const processor = new TelegramMessageProcessor({ gateway: gateway as any });

    await processor.handle({ ctx: ctx as any, userId: 456, text: "hello" });

    expect(gateway.handle).toHaveBeenCalledOnce();
  });

  it("replies with fallback if gateway.handle() throws", async () => {
    const gateway = makeGateway();
    gateway.handle.mockRejectedValue(new Error("provider down"));
    const ctx = makeCtx();
    const processor = new TelegramMessageProcessor({ gateway: gateway as any });

    await processor.handle({ ctx: ctx as any, userId: 456, text: "hello" });

    // Must not throw — should reply with fallback
    expect(ctx.reply).toHaveBeenCalled();
  });
});
