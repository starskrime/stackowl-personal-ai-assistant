import type { ToolImplementation, ToolContext } from "../registry.js";

export const IMessageTool: ToolImplementation = {
  definition: {
    name: "imessage",
    description:
      "Send and read iMessages/SMS via macOS Messages app. " +
      "Send texts, read recent conversations, search messages.",
    parameters: {
      type: "object",
      properties: {
        action: {
          type: "string",
          description:
            "Action: send, read_recent, read_chat, search, list_chats",
        },
        to: {
          type: "string",
          description: "Phone number or email for send action (e.g., '+1234567890')",
        },
        message: {
          type: "string",
          description: "Message text for send action",
        },
        query: {
          type: "string",
          description: "Search query for search action",
        },
        limit: {
          type: "number",
          description: "Number of messages to return (default: 10)",
        },
      },
      required: ["action"],
    },
  },

  category: "system",

  async execute(
    args: Record<string, unknown>,
    _context: ToolContext,
  ): Promise<string> {
    const action = String(args.action);
    const to = args.to as string | undefined;
    const message = args.message as string | undefined;
    const query = args.query as string | undefined;
    const limit = (args.limit as number) || 10;

    const { execFile } = await import("node:child_process");
    const { promisify } = await import("node:util");
    const exec = promisify(execFile);

    const osa = async (script: string): Promise<string> => {
      const { stdout } = await exec("osascript", ["-e", script], { timeout: 15000 });
      return stdout.trim();
    };

    const shell = async (cmd: string): Promise<string> => {
      const { stdout } = await exec("bash", ["-c", cmd], { timeout: 15000 });
      return stdout.trim();
    };

    try {
      switch (action) {
        case "send": {
          if (!to) return "Error: send requires 'to' (phone number or email).";
          if (!message) return "Error: send requires 'message' text.";
          const escapedMsg = message.replace(/"/g, '\\"');
          const escapedTo = to.replace(/"/g, '\\"');
          await osa(`
tell application "Messages"
  set targetService to 1st account whose service type = iMessage
  set targetBuddy to participant "${escapedTo}" of targetService
  send "${escapedMsg}" to targetBuddy
end tell`);
          return `✉️ Message sent to ${to}: "${message.length > 80 ? message.slice(0, 80) + "..." : message}"`;
        }

        case "read_recent": {
          // Read from Messages SQLite database
          const result = await shell(
            `sqlite3 ~/Library/Messages/chat.db "
SELECT
  datetime(m.date/1000000000 + 978307200, 'unixepoch', 'localtime') as date,
  CASE WHEN m.is_from_me = 1 THEN 'Me' ELSE COALESCE(h.id, 'Unknown') END as sender,
  m.text
FROM message m
LEFT JOIN handle h ON m.handle_id = h.ROWID
WHERE m.text IS NOT NULL
ORDER BY m.date DESC
LIMIT ${limit};" 2>/dev/null || echo "Cannot access Messages database. Grant Full Disk Access to your terminal app."`,
          );
          return `📬 Recent messages:\n${result}`;
        }

        case "read_chat": {
          if (!to) return "Error: read_chat requires 'to' (phone number or email).";
          const escapedTo = to.replace(/'/g, "''");
          const result = await shell(
            `sqlite3 ~/Library/Messages/chat.db "
SELECT
  datetime(m.date/1000000000 + 978307200, 'unixepoch', 'localtime') as date,
  CASE WHEN m.is_from_me = 1 THEN 'Me' ELSE h.id END as sender,
  m.text
FROM message m
JOIN handle h ON m.handle_id = h.ROWID
WHERE h.id LIKE '%${escapedTo}%' AND m.text IS NOT NULL
ORDER BY m.date DESC
LIMIT ${limit};" 2>/dev/null || echo "Cannot access Messages database."`,
          );
          return `📬 Chat with ${to}:\n${result}`;
        }

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
          return `🔍 Messages matching "${query}":\n${result}`;
        }

        case "list_chats": {
          const result = await shell(
            `sqlite3 ~/Library/Messages/chat.db "
SELECT DISTINCT
  h.id,
  datetime(MAX(m.date)/1000000000 + 978307200, 'unixepoch', 'localtime') as last_message
FROM handle h
JOIN message m ON m.handle_id = h.ROWID
GROUP BY h.id
ORDER BY MAX(m.date) DESC
LIMIT ${limit};" 2>/dev/null || echo "Cannot access Messages database."`,
          );
          return `📋 Recent chats:\n${result}`;
        }

        default:
          return (
            `Unknown action: "${action}". Available:\n` +
            `  send — Send a message (requires to, message)\n` +
            `  read_recent — Read most recent messages\n` +
            `  read_chat — Read messages from specific contact (requires to)\n` +
            `  search — Search messages (requires query)\n` +
            `  list_chats — List recent chat contacts`
          );
      }
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      if (msg.includes("not allowed")) {
        return "Permission denied. Grant Full Disk Access to your terminal in System Settings → Privacy & Security.";
      }
      return `Error (${action}): ${msg}`;
    }
  },
};
