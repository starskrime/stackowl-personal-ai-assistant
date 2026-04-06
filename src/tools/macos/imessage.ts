/**
 * StackOwl — iMessage Tool
 *
 * Send/receive iMessages, SMS, and RCS via the macOS Messages app.
 * Also supports sending file attachments (images, documents, etc).
 *
 * Architecture:
 *   - Text send: AppleScript via Messages dictionary
 *   - File send: AppleScript `send POSIX file "..."` — native attachment
 *   - Read/search: SQLite on ~/Library/Messages/chat.db (requires Full Disk Access)
 *
 * Permissions required:
 *   - Automation: Messages (for send)
 *   - Full Disk Access: terminal app (for read/search via SQLite)
 */

import { existsSync } from "node:fs";
import { resolve, isAbsolute } from "node:path";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import type { ToolImplementation, ToolContext } from "../registry.js";

const execFileAsync = promisify(execFile);

const osa = async (script: string): Promise<string> => {
  const { stdout } = await execFileAsync("osascript", ["-e", script], {
    timeout: 20_000,
  });
  return stdout.trim();
};

const shell = async (cmd: string): Promise<string> => {
  const { stdout } = await execFileAsync("bash", ["-c", cmd], {
    timeout: 15_000,
  });
  return stdout.trim();
};

export const IMessageTool: ToolImplementation = {
  definition: {
    name: "imessage",
    description:
      "Send and read iMessages/SMS via macOS Messages app. " +
      "Send text messages, send file attachments (images, documents, audio), " +
      "read recent conversations, search messages. " +
      "Use send_attachment to share a file (photo, PDF, etc) with a contact via iMessage.",
    parameters: {
      type: "object",
      properties: {
        action: {
          type: "string",
          description:
            "Action: send, send_attachment, read_recent, read_chat, search, list_chats",
        },
        to: {
          type: "string",
          description:
            "Phone number or Apple ID email for send actions (e.g. '+14155550100', 'user@icloud.com')",
        },
        message: {
          type: "string",
          description: "Message text for send action (optional alongside attachments)",
        },
        file_path: {
          type: "string",
          description:
            "Absolute or workspace-relative path to the file to send as attachment. " +
            "Used by send_attachment. Supports images, PDFs, documents, audio files.",
        },
        query: {
          type: "string",
          description: "Search query for search action",
        },
        limit: {
          type: "number",
          description: "Number of messages/chats to return (default 10)",
        },
      },
      required: ["action"],
    },
  },

  category: "system",

  async execute(
    args: Record<string, unknown>,
    context: ToolContext,
  ): Promise<string> {
    const action = String(args.action);
    const to = args.to as string | undefined;
    const message = args.message as string | undefined;
    const filePath = args.file_path as string | undefined;
    const query = args.query as string | undefined;
    const limit = (args.limit as number) || 10;

    try {
      switch (action) {

        // ── Send text message ───────────────────────────────────────────
        case "send": {
          if (!to) return "Error: send requires 'to' (phone number or Apple ID email).";
          if (!message) return "Error: send requires 'message' text.";

          const escapedMsg = message.replace(/\\/g, "\\\\").replace(/"/g, '\\"');
          const escapedTo = to.replace(/"/g, '\\"');

          // Try iMessage account first, fall back to SMS
          await osa(`
tell application "Messages"
  set targetSvc to missing value
  repeat with a in (every account)
    try
      if service type of a = iMessage then
        set targetSvc to a
        exit repeat
      end if
    end try
  end repeat
  if targetSvc is missing value then
    repeat with a in (every account)
      try
        if service type of a = SMS then
          set targetSvc to a
          exit repeat
        end if
      end try
    end repeat
  end if
  if targetSvc is missing value then error "No iMessage or SMS account found"
  send "${escapedMsg}" to buddy "${escapedTo}" of targetSvc
end tell`);

          return `Message sent to ${to}: "${message.length > 80 ? message.slice(0, 80) + "..." : message}"`;
        }

        // ── Send file attachment ────────────────────────────────────────
        case "send_attachment": {
          if (!to) return "Error: send_attachment requires 'to' (phone number or Apple ID email).";
          if (!filePath) return "Error: send_attachment requires 'file_path'.";

          const cwd = context.cwd || process.cwd();
          const absPath = isAbsolute(filePath)
            ? filePath
            : resolve(cwd, filePath);

          if (!existsSync(absPath)) {
            return `Error: file not found at "${absPath}".`;
          }

          const escapedPath = absPath.replace(/\\/g, "\\\\").replace(/"/g, '\\"');
          const escapedTo = to.replace(/"/g, '\\"');
          // Optional text alongside the attachment
          const escapedMsg = message
            ? message.replace(/\\/g, "\\\\").replace(/"/g, '\\"')
            : "";

          await osa(`
tell application "Messages"
  set targetSvc to missing value
  repeat with a in (every account)
    try
      if service type of a = iMessage then
        set targetSvc to a
        exit repeat
      end if
    end try
  end repeat
  if targetSvc is missing value then error "No iMessage account found — file attachments require iMessage"
  set targetBuddy to buddy "${escapedTo}" of targetSvc
  send POSIX file "${escapedPath}" to targetBuddy
  ${escapedMsg ? `send "${escapedMsg}" to targetBuddy` : ""}
end tell`);

          const fileName = absPath.split("/").pop() ?? absPath;
          return (
            `Attachment sent to ${to}: ${fileName}` +
            (message ? `\nWith message: "${message}"` : "")
          );
        }

        // ── Read recent messages ────────────────────────────────────────
        case "read_recent": {
          const result = await shell(
            `sqlite3 ~/Library/Messages/chat.db "
SELECT
  datetime(m.date/1000000000 + 978307200, 'unixepoch', 'localtime') as date,
  CASE WHEN m.is_from_me = 1 THEN 'Me' ELSE COALESCE(h.id, 'Unknown') END as sender,
  COALESCE(m.text, '[attachment]') as text
FROM message m
LEFT JOIN handle h ON m.handle_id = h.ROWID
WHERE m.text IS NOT NULL OR m.cache_has_attachments = 1
ORDER BY m.date DESC
LIMIT ${limit};" 2>/dev/null || echo "Cannot access Messages database. Grant Full Disk Access to your terminal in System Settings → Privacy & Security."`,
          );
          return `Recent messages:\n${result}`;
        }

        // ── Read messages from specific contact ─────────────────────────
        case "read_chat": {
          if (!to) return "Error: read_chat requires 'to' (phone number or email).";
          const escapedTo = to.replace(/'/g, "''");
          const result = await shell(
            `sqlite3 ~/Library/Messages/chat.db "
SELECT
  datetime(m.date/1000000000 + 978307200, 'unixepoch', 'localtime') as date,
  CASE WHEN m.is_from_me = 1 THEN 'Me' ELSE h.id END as sender,
  COALESCE(m.text, '[attachment]') as text
FROM message m
JOIN handle h ON m.handle_id = h.ROWID
WHERE h.id LIKE '%${escapedTo}%'
ORDER BY m.date DESC
LIMIT ${limit};" 2>/dev/null || echo "Cannot access Messages database."`,
          );
          return `Chat with ${to}:\n${result}`;
        }

        // ── Search messages ─────────────────────────────────────────────
        case "search": {
          if (!query) return "Error: search requires 'query'.";
          const escapedQuery = query.replace(/'/g, "''");
          const result = await shell(
            `sqlite3 ~/Library/Messages/chat.db "
SELECT
  datetime(m.date/1000000000 + 978307200, 'unixepoch', 'localtime') as date,
  CASE WHEN m.is_from_me = 1 THEN 'Me' ELSE COALESCE(h.id, 'Unknown') END as sender,
  m.text
FROM message m
LEFT JOIN handle h ON m.handle_id = h.ROWID
WHERE m.text LIKE '%${escapedQuery}%'
ORDER BY m.date DESC
LIMIT ${limit};" 2>/dev/null || echo "Cannot access Messages database."`,
          );
          return `Messages matching "${query}":\n${result}`;
        }

        // ── List recent chats ───────────────────────────────────────────
        case "list_chats": {
          const result = await shell(
            `sqlite3 ~/Library/Messages/chat.db "
SELECT DISTINCT
  h.id as contact,
  datetime(MAX(m.date)/1000000000 + 978307200, 'unixepoch', 'localtime') as last_message
FROM handle h
JOIN message m ON m.handle_id = h.ROWID
GROUP BY h.id
ORDER BY MAX(m.date) DESC
LIMIT ${limit};" 2>/dev/null || echo "Cannot access Messages database."`,
          );
          return `Recent chats:\n${result}`;
        }

        default:
          return (
            `Unknown action: "${action}". Available:\n` +
            `  send              — Send text message (requires to, message)\n` +
            `  send_attachment   — Send a file/image (requires to, file_path; message optional)\n` +
            `  read_recent       — Read most recent messages across all chats\n` +
            `  read_chat         — Read messages from specific contact (requires to)\n` +
            `  search            — Search messages by text (requires query)\n` +
            `  list_chats        — List recent chat contacts`
          );
      }
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);

      if (msg.includes("not allowed") || msg.includes("assistive access")) {
        return (
          "Permission denied: macOS Automation access required.\n" +
          "Go to: System Settings → Privacy & Security → Automation\n" +
          "Enable Messages access for your terminal app."
        );
      }
      if (msg.includes("Full Disk") || msg.includes("database")) {
        return (
          "Cannot read Messages database.\n" +
          "Go to: System Settings → Privacy & Security → Full Disk Access\n" +
          "Add your terminal app (Terminal, iTerm2, VS Code)."
        );
      }
      if (msg.includes("No iMessage account") || msg.includes("No account")) {
        return (
          `iMessage account not found or not signed in.\n` +
          `Open Messages.app and sign in to your Apple ID, then try again.\n` +
          `Error: ${msg}`
        );
      }
      return `Error (${action}): ${msg}`;
    }
  },
};
