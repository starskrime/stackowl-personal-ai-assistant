import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
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
