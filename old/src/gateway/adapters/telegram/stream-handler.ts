import type { Api } from "grammy";
import { log } from "../../../logger.js";
import type { StreamEvent } from "../../../providers/base.js";
import { convertTables } from "../../formatters/table-converter.js";
import { TELEGRAM_LIMITS } from "./constants.js";

// ─── Public API ────────────────────────────────────────────────────────────────

export interface StreamHandlerOptions {
  chatId: number;
  botApi: Api;
  suppressThinking: boolean;
  initialMessageId?: number;
  onStreamClaimed?: () => void;
}

export interface StreamHandlerStatus {
  streamedContent: string;
  streamedContentWithHeader: string;
  messageId: number | null;
  finalResponseSent: boolean;
}

/**
 * Manages in-place streaming of an AI response into a Telegram message.
 *
 * Created once per message — never shared between concurrent users.
 * Extracted from the 212-line `createStreamHandler()` closure in
 * `telegram.ts` (Task 4 of the TelegramAdapter refactor plan).
 *
 * Responsibilities:
 *  - Accumulate `text_delta` events into plain-text `pureContent`
 *  - Create the initial Telegram message on the first non-empty chunk
 *  - Throttle `editMessageText` calls to avoid Telegram rate-limits
 *  - Deduplicate edits (skip when rendered output is unchanged)
 *  - Strip `<thinking>` / `<reasoning>` / … tags before delivery
 *  - Track `editFailures` and bail out after `MAX_EDIT_FAILURES`
 *  - Expose `pushToolStatus()` for inline tool-progress lines
 */
export class TelegramStreamHandler {
  // ── Public observable state ─────────────────────────────────────────────────
  readonly status: StreamHandlerStatus = {
    streamedContent: "",
    streamedContentWithHeader: "",
    messageId: null,
    finalResponseSent: false,
  };

  readonly suppressThinking: boolean;

  // ── Private rendering state ─────────────────────────────────────────────────
  private readonly chatId: number;
  private readonly botApi: Api;
  private readonly onStreamClaimed: (() => void) | undefined;

  /** Full text shown in Telegram — includes tool-status lines (HTML). */
  private displayText = "";
  /** Only text_delta content — plain text, no tool noise. */
  private pureContent = "";
  /** Timestamp of the last successful editMessageText call. */
  private lastEditTime = 0;
  /** Pending throttle timer handle. */
  private pendingEdit: ReturnType<typeof setTimeout> | null = null;
  /** Whether any tool-status lines have been injected. */
  private hasToolStatus = false;
  /** Whether actual response content has started after tool-status. */
  private contentStarted = false;
  /** Consecutive edit-failure counter. */
  private editFailures = 0;
  /** Guard against duplicate sendMessage() calls when pushToolStatus() fires concurrently. */
  private sendingInitial = false;
  /** Whether the first streaming message has been sent successfully. */
  private initialMessageDelivered = false;
  /** Guard so `onStreamClaimed` fires exactly once. */
  private streamClaimedFired = false;
  /** The last rendered string sent to Telegram — used for dedup. */
  private previousRendered = "";
  /** Telegram message id for in-place edits. */
  private messageId: number | null;

  // ── Constructor ─────────────────────────────────────────────────────────────

  constructor(opts: StreamHandlerOptions) {
    log.telegram.debug("stream-handler.constructor: entry", {
      chatId: opts.chatId,
      suppressThinking: opts.suppressThinking,
      initialMessageId: opts.initialMessageId ?? null,
    });

    this.chatId = opts.chatId;
    this.botApi = opts.botApi;
    this.suppressThinking = opts.suppressThinking;
    this.onStreamClaimed = opts.onStreamClaimed;
    this.messageId = opts.initialMessageId ?? null;

    if (this.messageId !== null) {
      this.status.messageId = this.messageId;
    }

    log.telegram.debug("stream-handler.constructor: exit", {
      initialMessageId: this.messageId,
    });
  }

  // ── Public entry point ──────────────────────────────────────────────────────

  /**
   * Dispatch a single StreamEvent from the provider's async generator.
   * Callers can `await handler.handle(event)` in their for-await loop.
   */
  async handle(event: StreamEvent): Promise<void> {
    log.telegram.debug("stream-handler.handle: entry", { type: event.type });

    switch (event.type) {
      case "text_delta":
        await this.handleTextDelta(event.content);
        break;
      case "done":
        this.handleDone(event);
        break;
      case "tool_start":
      case "tool_args_delta":
      case "tool_end":
        // Tool execution progress is managed externally via pushToolStatus().
        break;
      default: {
        // Exhaustiveness guard — new event types are safely ignored.
        const _exhaustive: never = event;
        log.telegram.debug("stream-handler.handle: unknown event type, skipping", {
          type: (_exhaustive as { type: string }).type,
        });
      }
    }

    log.telegram.debug("stream-handler.handle: exit", { type: event.type });
  }

  // ── Tool status injection ───────────────────────────────────────────────────

  /**
   * Push a tool-execution status line into the streaming message (edit-in-place).
   * Called from the host adapter's `onProgress` callback for tool events so they
   * appear inline instead of as separate Telegram messages.
   */
  pushToolStatus(msg: string): void {
    log.telegram.debug("stream-handler.pushToolStatus: entry", { msgLen: msg.length });

    const html = this.escHtml(msg)
      .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
      .replace(/`(.+?)`/g, "<code>$1</code>");

    this.displayText += `\n${html}`;
    this.hasToolStatus = true;

    // Best-effort throttled edit — ignore errors (flushEdit logs internally).
    this.flushEdit().catch((_err) => {
      log.telegram.warn("stream-handler.pushToolStatus: flushEdit failed (suppressed)", _err as Error);
    });

    // If no streaming message exists yet, create one now.
    // sendingInitial guards against duplicate sendMessage() calls when
    // pushToolStatus() is invoked concurrently before the first send resolves.
    if (!this.messageId && !this.sendingInitial) {
      this.sendingInitial = true;
      this.botApi
        .sendMessage(this.chatId, this.displayText || "...", { parse_mode: "HTML" })
        .then((sent) => {
          this.messageId = sent.message_id;
          this.status.messageId = sent.message_id;
          this.lastEditTime = Date.now();
          log.telegram.debug("stream-handler.pushToolStatus: initial message created", {
            messageId: sent.message_id,
          });
        })
        .catch((err) => {
          log.telegram.warn("stream-handler.pushToolStatus: initial send failed", err as Error);
        })
        .finally(() => {
          this.sendingInitial = false;
        });
    }

    log.telegram.debug("stream-handler.pushToolStatus: exit");
  }

  // ── Flush ───────────────────────────────────────────────────────────────────

  /**
   * Attempt an immediate `editMessageText` with the current rendered content.
   *
   * Guards applied (in order):
   *  1. No messageId yet → skip
   *  2. pureContent is empty → skip
   *  3. Too many consecutive failures → skip (non-streaming fallback)
   *  4. Rendered output identical to previous send → skip (dedup)
   */
  async flushEdit(): Promise<void> {
    log.telegram.debug("stream-handler.flushEdit: entry", {
      messageId: this.messageId,
      pureLen: this.pureContent.length,
      editFailures: this.editFailures,
    });

    // Guard 1: no message yet
    if (!this.messageId) {
      log.telegram.debug("stream-handler.flushEdit: skip — no messageId");
      return;
    }
    // Guard 2: nothing to show
    if (!this.pureContent.trim()) {
      log.telegram.debug("stream-handler.flushEdit: skip — empty content");
      return;
    }
    // Guard 3: too many failures
    if (this.editFailures >= TELEGRAM_LIMITS.MAX_EDIT_FAILURES) {
      log.telegram.debug("stream-handler.flushEdit: skip — failure ceiling reached", {
        editFailures: this.editFailures,
      });
      return;
    }

    // NOTE: When hasToolStatus is true, displayText is used directly.
    // This means multi-line markdown tables in responses following tool-status
    // lines won't be converted. This preserves the original closure behavior.
    // TODO: Rebuild displayText from tool header + renderContent(pureContent)
    // to fix table rendering in tool-heavy responses.
    const rendered = this.hasToolStatus
      ? this.displayText // tool-status lines are already HTML — keep as-is
      : this.renderContent(this.pureContent);

    // Guard 4: dedup — skip if the rendered text has not changed
    if (!rendered.trim() || rendered === this.previousRendered) {
      log.telegram.debug("stream-handler.flushEdit: skip — identical render (dedup)");
      return;
    }
    this.previousRendered = rendered;

    log.telegram.debug("stream-handler.flushEdit: step — calling editMessageText", {
      messageId: this.messageId,
      renderedLen: rendered.length,
    });

    try {
      await this.botApi.editMessageText(this.chatId, this.messageId, rendered, {
        parse_mode: "HTML",
      });
      this.lastEditTime = Date.now();
      this.editFailures = 0;
      log.telegram.debug("stream-handler.flushEdit: exit — edit succeeded", {
        messageId: this.messageId,
      });
    } catch (err) {
      this.editFailures++;
      const errMsg = err instanceof Error ? err.message : String(err);

      // Benign Telegram errors — reset failure counter so we keep trying.
      if (
        errMsg.includes("message is not modified") ||
        errMsg.includes("message to edit not found")
      ) {
        this.editFailures = 0;
        log.telegram.debug("stream-handler.flushEdit: benign edit skip", { reason: errMsg });
        return;
      }

      log.telegram.warn("stream-handler.flushEdit: editMessageText failed", err as Error, {
        editFailures: this.editFailures,
        ceiling: TELEGRAM_LIMITS.MAX_EDIT_FAILURES,
      });

      if (this.editFailures >= TELEGRAM_LIMITS.MAX_EDIT_FAILURES) {
        log.telegram.warn(
          "stream-handler.flushEdit: failure ceiling reached — switching to non-streaming delivery",
          { failures: this.editFailures },
        );
      }
    }
  }

  // ── Rendering helpers (public for reuse by TelegramMessageProcessor) ────────

  /**
   * Full-pipeline render: strip internal tags → convert tables → HTML-escape → apply markdown.
   *
   * Used for every throttled edit so the streaming display is always
   * fully formatted (tables, headings, blockquotes) — not just the final edit.
   */
  renderContent(text: string): string {
    const clean = this.stripInternalTags(text);
    const converted = convertTables(clean); // plain text + **bold** only, no HTML
    return this.escHtml(converted)
      .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
      .replace(/(?<!\*)\*([^*\n]+?)\*(?!\*)/g, "<i>$1</i>")
      .replace(/`(.+?)`/g, "<code>$1</code>");
  }

  /**
   * Strip LLM-internal reasoning tags from user-visible output.
   * Called from `handleTextDelta()` only when `suppressThinking` is true.
   * When false, thinking tags pass through so callers can decide whether
   * to surface or discard them.
   */
  stripInternalTags(text: string): string {
    return text
      .replace(/<inline_thought>[\s\S]*?<\/inline_thought>/gi, "")
      .replace(/<think>[\s\S]*?<\/think>/gi, "")
      .replace(/<reasoning>[\s\S]*?<\/reasoning>/gi, "")
      .replace(/<scratchpad>[\s\S]*?<\/scratchpad>/gi, "")
      .replace(/<reflection>[\s\S]*?<\/reflection>/gi, "")
      .replace(/<thinking>[\s\S]*?<\/thinking>/gi, "")
      .replace(/<memo>[\s\S]*?<\/memo>/gi, "")
      .replace(/\n{3,}/g, "\n\n")
      .trim();
  }

  /** Escape text for Telegram HTML mode. */
  escHtml(text: string): string {
    return text
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  // ── Private helpers ─────────────────────────────────────────────────────────

  private async handleTextDelta(rawContent: string): Promise<void> {
    // Strip the engine's internal [DONE] marker — it's not user content.
    let chunk = rawContent.replace(/\[DONE\]/g, "");
    if (!chunk) return;

    // Strip internal reasoning tags. The suppressThinking field is reserved
    // for future per-user configuration — currently tags are always stripped.
    chunk = this.stripInternalTags(chunk);
    if (!chunk) return;

    // Notify the host that this stream has claimed the ACK message slot.
    if (!this.streamClaimedFired) {
      this.streamClaimedFired = true;
      this.onStreamClaimed?.();
    }

    // Insert visual separator when content follows tool-status lines.
    if (this.hasToolStatus && !this.contentStarted) {
      this.displayText += "\n\n";
      this.contentStarted = true;
    }

    // displayText is HTML — convert inline markdown as we accumulate.
    this.displayText += this.chunkToHtml(chunk);
    // pureContent stays plain text — used for dedup detection and final export.
    this.pureContent += chunk;
    this.status.streamedContent = this.pureContent;

    if (!this.messageId) {
      // First chunk — send the initial message.
      log.telegram.debug("stream-handler.handleTextDelta: sending initial message", {
        chatId: this.chatId,
        displayLen: this.displayText.length,
      });
      try {
        const sent = await this.botApi.sendMessage(
          this.chatId,
          this.displayText || "...",
          { parse_mode: "HTML" },
        );
        this.messageId = sent.message_id;
        this.status.messageId = sent.message_id;
        this.status.streamedContentWithHeader = this.displayText;
        this.lastEditTime = Date.now();
        this.initialMessageDelivered = true;
        log.telegram.debug("stream-handler.handleTextDelta: initial message sent", {
          messageId: this.messageId,
        });
      } catch (err) {
        log.telegram.warn(
          "stream-handler.handleTextDelta: initial send failed — will fall back to final response",
          err as Error,
          { chatId: this.chatId },
        );
      }
      return;
    }

    // Throttle subsequent edits.
    const elapsed = Date.now() - this.lastEditTime;
    if (elapsed >= TELEGRAM_LIMITS.STREAM_THROTTLE_MS) {
      if (this.pendingEdit) {
        clearTimeout(this.pendingEdit);
        this.pendingEdit = null;
      }
      await this.flushEdit();
    } else if (!this.pendingEdit) {
      this.pendingEdit = setTimeout(() => {
        this.pendingEdit = null;
        this.flushEdit().catch((err) => {
          log.telegram.warn("stream-handler.handleTextDelta: scheduled flushEdit failed", err as Error);
        });
      }, TELEGRAM_LIMITS.STREAM_THROTTLE_MS - elapsed);
    }
  }

  private handleDone(event: { type: "done"; usage?: unknown }): void {
    log.telegram.debug("stream-handler.handleDone: entry", {
      initialDelivered: this.initialMessageDelivered,
      pureLen: this.pureContent.length,
      usage: event.usage ?? null,
    });

    this.displayText = this.displayText.replace(/\[DONE\]/g, "").trimEnd();
    this.pureContent = this.pureContent.replace(/\[DONE\]/g, "").trimEnd();
    this.status.streamedContent = this.pureContent;
    this.status.finalResponseSent = true;

    // Cancel any pending throttled edit.
    // The post-handle code (TelegramMessageProcessor / TelegramAdapter) will
    // perform the authoritative final edit that includes the owl header.
    // Calling flushEdit() here would overwrite that header.
    if (this.pendingEdit) {
      clearTimeout(this.pendingEdit);
      this.pendingEdit = null;
    }

    log.telegram.debug("stream-handler.handleDone: exit", {
      pureLen: this.pureContent.length,
    });
  }

  /**
   * Convert a streaming chunk to HTML inline.
   * Used only for the initial message (first few chars).
   * All throttled edits use `renderContent()` on the full `pureContent`
   * so tables/headings are always properly converted.
   */
  private chunkToHtml(raw: string): string {
    return this.escHtml(raw)
      .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
      .replace(/(?<!\*)\*([^*\n]+?)\*(?!\*)/g, "<i>$1</i>")
      .replace(/`(.+?)`/g, "<code>$1</code>");
  }
}
