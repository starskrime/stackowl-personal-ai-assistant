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
      log.telegram.warn("telegram-progress-notifier: start called without bindSession", undefined, { turnId });
      return;
    }

    log.telegram.debug("telegram-progress-notifier: start: entry", { turnId, chatId: session.chatId });

    // Send initial ACK message in the random language.
    try {
      const sent = await this.api.sendMessage(
        session.chatId,
        `<i>${escHtml(phrase)}</i>`,
        { parse_mode: "HTML" },
      );
      session.messageId = sent.message_id;
      log.telegram.debug("telegram-progress-notifier: start: ACK sent", { turnId, messageId: session.messageId });
    } catch (err) {
      log.telegram.warn("telegram-progress-notifier: start: sendMessage failed", err, { turnId });
    }

    // Send initial typing action, then refresh every 4000ms.
    try {
      await this.api.sendChatAction(session.chatId, "typing");
    } catch (err) {
      log.telegram.warn("telegram-progress-notifier: start: sendChatAction failed", err, { turnId });
    }

    session.timer = setInterval(() => {
      this.api.sendChatAction(session.chatId, "typing").catch((err) => {
        log.telegram.warn("telegram-progress-notifier: typing refresh failed", err, { turnId });
      });
    }, 4000);

    log.telegram.debug("telegram-progress-notifier: start: exit", { turnId });
  }

  async update(text: string, turnId: string): Promise<void> {
    const session = this.sessions.get(turnId);
    if (!session?.messageId) return;
    if (session.streamClaimed) return; // stream owns the message now

    log.telegram.debug("telegram-progress-notifier: update: entry", { turnId, textLen: text.length });

    try {
      await this.api.editMessageText(session.chatId, session.messageId, text, {
        parse_mode: "HTML",
      });
      log.telegram.debug("telegram-progress-notifier: update: exit", { turnId });
    } catch (err) {
      log.telegram.warn("telegram-progress-notifier: update: editMessageText failed", err, { turnId });
    }
  }

  async stop(turnId: string): Promise<void> {
    const session = this.sessions.get(turnId);
    if (!session) return;

    log.telegram.debug("telegram-progress-notifier: stop: entry", { turnId, streamClaimed: session.streamClaimed });

    if (session.timer) {
      clearInterval(session.timer);
      session.timer = null;
      log.telegram.debug("telegram-progress-notifier: stop: typing timer cleared", { turnId });
    }

    // Delete the ACK message only if the stream never claimed it.
    // If claimed, the message now contains the response — do not delete.
    if (!session.streamClaimed && session.messageId) {
      try {
        await this.api.deleteMessage(session.chatId, session.messageId);
        log.telegram.debug("telegram-progress-notifier: stop: ACK deleted", { turnId });
      } catch (err) {
        log.telegram.warn("telegram-progress-notifier: stop: deleteMessage failed", err, { turnId });
      }
    }

    this.sessions.delete(turnId);
    log.telegram.debug("telegram-progress-notifier: stop: exit", { turnId });
  }
}

function escHtml(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
