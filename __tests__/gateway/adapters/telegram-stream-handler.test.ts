import { describe, it, expect, vi, beforeEach } from "vitest";
import { TelegramStreamHandler } from "../../../src/gateway/adapters/telegram/stream-handler.js";

// ─── Mock grammy Api ────────────────────────────────────────────────────────

const makeMockBotApi = () => ({
  sendMessage: vi.fn().mockResolvedValue({ message_id: 42 }),
  editMessageText: vi.fn().mockResolvedValue(true),
});

// ─── Tests ───────────────────────────────────────────────────────────────────

describe("TelegramStreamHandler", () => {
  it("constructs without error", () => {
    const api = makeMockBotApi();
    const handler = new TelegramStreamHandler({
      chatId: 123,
      botApi: api as any,
      suppressThinking: false,
    });
    expect(handler).toBeDefined();
    expect(handler.status.streamedContent).toBe("");
    expect(handler.status.messageId).toBeNull();
    expect(handler.status.finalResponseSent).toBe(false);
  });

  it("constructs with initialMessageId and pre-populates status.messageId", () => {
    const api = makeMockBotApi();
    const handler = new TelegramStreamHandler({
      chatId: 123,
      botApi: api as any,
      suppressThinking: false,
      initialMessageId: 99,
    });
    expect(handler.status.messageId).toBe(99);
  });

  it("flushEdit skips empty content", async () => {
    const api = makeMockBotApi();
    const handler = new TelegramStreamHandler({
      chatId: 123,
      botApi: api as any,
      suppressThinking: false,
    });
    // Force messageId so flushEdit would normally attempt an edit
    await handler.handle({ type: "text_delta", content: "" });
    expect(api.editMessageText).not.toHaveBeenCalled();
    expect(api.sendMessage).not.toHaveBeenCalled();
  });

  it("flushEdit skips unchanged content (dedup)", async () => {
    const api = makeMockBotApi();
    const handler = new TelegramStreamHandler({
      chatId: 123,
      botApi: api as any,
      suppressThinking: false,
    });

    // First delta — sends the initial message (no edit yet, messageId is null)
    await handler.handle({ type: "text_delta", content: "hello world" });
    expect(api.sendMessage).toHaveBeenCalledTimes(1);
    const editCallsAfterFirst = api.editMessageText.mock.calls.length;

    // Second delta with no new content (empty after strip) — should not trigger extra edits
    await handler.handle({ type: "text_delta", content: "" });
    expect(api.editMessageText.mock.calls.length).toBe(editCallsAfterFirst);
  });

  it("stripInternalTags removes thinking tags when suppressThinking is true", async () => {
    const api = makeMockBotApi();
    const handler = new TelegramStreamHandler({
      chatId: 123,
      botApi: api as any,
      suppressThinking: true,
    });

    await handler.handle({
      type: "text_delta",
      content: "<thinking>internal</thinking>visible",
    });

    // The initial message should have been sent
    expect(api.sendMessage).toHaveBeenCalledTimes(1);
    const sentContent = api.sendMessage.mock.calls[0][1] as string;
    expect(sentContent).not.toContain("internal");
    expect(sentContent).toContain("visible");
  });

  it("stripInternalTags removes thinking tags even when suppressThinking is false", async () => {
    const api = makeMockBotApi();
    const handler = new TelegramStreamHandler({
      chatId: 123,
      botApi: api as any,
      suppressThinking: false,
    });

    await handler.handle({
      type: "text_delta",
      content: "<thinking>secret reasoning</thinking>user answer",
    });

    expect(api.sendMessage).toHaveBeenCalledTimes(1);
    const sentContent = api.sendMessage.mock.calls[0][1] as string;
    expect(sentContent).not.toContain("secret reasoning");
    expect(sentContent).toContain("user answer");
  });

  it("status.streamedContent tracks accumulated text", async () => {
    const api = makeMockBotApi();
    const handler = new TelegramStreamHandler({
      chatId: 123,
      botApi: api as any,
      suppressThinking: false,
    });

    await handler.handle({ type: "text_delta", content: "hello" });
    await handler.handle({ type: "text_delta", content: "world" });

    expect(handler.status.streamedContent).toBe("helloworld");
  });

  it("status.finalResponseSent is set on done event", async () => {
    const api = makeMockBotApi();
    const handler = new TelegramStreamHandler({
      chatId: 123,
      botApi: api as any,
      suppressThinking: false,
    });

    expect(handler.status.finalResponseSent).toBe(false);
    await handler.handle({ type: "done" });
    expect(handler.status.finalResponseSent).toBe(true);
  });

  it("onStreamClaimed fires exactly once on first non-empty chunk", async () => {
    const api = makeMockBotApi();
    const onStreamClaimed = vi.fn();
    const handler = new TelegramStreamHandler({
      chatId: 123,
      botApi: api as any,
      suppressThinking: false,
      onStreamClaimed,
    });

    await handler.handle({ type: "text_delta", content: "first" });
    expect(onStreamClaimed).toHaveBeenCalledTimes(1);

    await handler.handle({ type: "text_delta", content: "second" });
    expect(onStreamClaimed).toHaveBeenCalledTimes(1); // still once
  });

  it("onStreamClaimed does not fire for empty chunks", async () => {
    const api = makeMockBotApi();
    const onStreamClaimed = vi.fn();
    const handler = new TelegramStreamHandler({
      chatId: 123,
      botApi: api as any,
      suppressThinking: false,
      onStreamClaimed,
    });

    await handler.handle({ type: "text_delta", content: "" });
    await handler.handle({ type: "text_delta", content: "   " });
    // All-whitespace-after-strip won't fire the callback
    expect(onStreamClaimed).not.toHaveBeenCalled();
  });

  it("done event strips [DONE] markers from accumulated content", async () => {
    const api = makeMockBotApi();
    const handler = new TelegramStreamHandler({
      chatId: 123,
      botApi: api as any,
      suppressThinking: false,
    });

    await handler.handle({ type: "text_delta", content: "some text" });
    await handler.handle({ type: "done" });

    expect(handler.status.streamedContent).not.toContain("[DONE]");
    expect(handler.status.finalResponseSent).toBe(true);
  });

  it("stripInternalTags removes all known reasoning tag variants", () => {
    const api = makeMockBotApi();
    const handler = new TelegramStreamHandler({
      chatId: 123,
      botApi: api as any,
      suppressThinking: false,
    });

    const tags: Array<[string, string]> = [
      ["<thinking>__HIDDEN__</thinking>", "__HIDDEN__"],
      ["<think>__HIDDEN__</think>", "__HIDDEN__"],
      ["<reasoning>__HIDDEN__</reasoning>", "__HIDDEN__"],
      ["<scratchpad>__HIDDEN__</scratchpad>", "__HIDDEN__"],
      ["<reflection>__HIDDEN__</reflection>", "__HIDDEN__"],
      ["<inline_thought>__HIDDEN__</inline_thought>", "__HIDDEN__"],
      ["<memo>__HIDDEN__</memo>", "__HIDDEN__"],
    ];

    for (const [input, hidden] of tags) {
      const result = handler.stripInternalTags(`before ${input} after`);
      expect(result).not.toContain(hidden);
      expect(result).toContain("before");
      expect(result).toContain("after");
    }
  });

  it("escHtml escapes &, <, >", () => {
    const api = makeMockBotApi();
    const handler = new TelegramStreamHandler({
      chatId: 123,
      botApi: api as any,
      suppressThinking: false,
    });

    expect(handler.escHtml("a & b < c > d")).toBe("a &amp; b &lt; c &gt; d");
  });

  it("renderContent applies markdown → HTML pipeline", () => {
    const api = makeMockBotApi();
    const handler = new TelegramStreamHandler({
      chatId: 123,
      botApi: api as any,
      suppressThinking: false,
    });

    const result = handler.renderContent("**bold** and `code`");
    expect(result).toContain("<b>bold</b>");
    expect(result).toContain("<code>code</code>");
  });

  it("pushToolStatus creates initial message when no messageId exists", async () => {
    const api = makeMockBotApi();
    const handler = new TelegramStreamHandler({
      chatId: 123,
      botApi: api as any,
      suppressThinking: false,
    });

    handler.pushToolStatus("**Running** tool `shell`");

    // Allow microtask/promise to resolve
    await new Promise((r) => setTimeout(r, 10));

    expect(api.sendMessage).toHaveBeenCalledTimes(1);
    const sentText = api.sendMessage.mock.calls[0][1] as string;
    expect(sentText).toContain("<b>Running</b>");
    expect(sentText).toContain("<code>shell</code>");
  });

  it("handle ignores tool_start and tool_end without throwing", async () => {
    const api = makeMockBotApi();
    const handler = new TelegramStreamHandler({
      chatId: 123,
      botApi: api as any,
      suppressThinking: false,
    });

    await expect(
      handler.handle({ type: "tool_start", toolCallId: "tc1", toolName: "shell" }),
    ).resolves.toBeUndefined();

    await expect(
      handler.handle({
        type: "tool_end",
        toolCallId: "tc1",
        toolName: "shell",
        arguments: {},
      }),
    ).resolves.toBeUndefined();
  });
});
