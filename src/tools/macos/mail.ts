import type { ToolImplementation, ToolContext } from "../registry.js";
import { exec } from "node:child_process";
import { promisify } from "node:util";

const execAsync = promisify(exec);

function escapeForShell(str: string): string {
  return str.replace(/'/g, "'\\''");
}

export const AppleMailTool: ToolImplementation = {
  definition: {
    name: "apple_mail",
    description:
      "Send and read email via macOS Mail.app. Can send messages, check unread count, and read recent emails.",
    parameters: {
      type: "object",
      properties: {
        action: {
          type: "string",
          enum: ["send", "check", "read"],
          description:
            "Action to perform: send an email, check unread count, or read recent unread emails.",
        },
        to: {
          type: "string",
          description: "Recipient email address (required for 'send').",
        },
        subject: {
          type: "string",
          description: "Email subject (required for 'send').",
        },
        body: {
          type: "string",
          description: "Email body content (required for 'send').",
        },
      },
      required: ["action"],
    },
  },

  async execute(
    args: Record<string, unknown>,
    _context: ToolContext,
  ): Promise<string> {
    const action = args.action as string;

    try {
      switch (action) {
        case "send": {
          const to = args.to as string;
          const subject = args.subject as string;
          const body = args.body as string;

          if (!to || !subject || !body) {
            return "Error: 'send' action requires to, subject, and body parameters.";
          }

          const script = `
tell application "Mail"
    set newMessage to make new outgoing message with properties {subject:"${escapeForShell(subject)}", content:"${escapeForShell(body)}", visible:true}
    tell newMessage
        make new to recipient at end of to recipients with properties {address:"${escapeForShell(to)}"}
    end tell
    send newMessage
    return "Email sent to ${escapeForShell(to)} with subject: ${escapeForShell(subject)}"
end tell`;
          const { stdout } = await execAsync(
            `osascript -e '${escapeForShell(script)}'`,
            { timeout: 15000 },
          );
          return stdout.trim() || `Email sent to ${to}.`;
        }

        case "check": {
          const script = `
tell application "Mail"
    set unreadCount to 0
    repeat with acc in accounts
        repeat with mb in mailboxes of acc
            set unreadCount to unreadCount + (unread count of mb)
        end repeat
    end repeat
    return "Unread emails: " & unreadCount
end tell`;
          const { stdout } = await execAsync(
            `osascript -e '${escapeForShell(script)}'`,
            { timeout: 15000 },
          );
          return stdout.trim() || "Unable to check unread count.";
        }

        case "read": {
          const script = `
tell application "Mail"
    set output to ""
    set inboxMessages to messages of inbox
    set readCount to 0
    repeat with msg in inboxMessages
        if read status of msg is false then
            set msgFrom to sender of msg
            set msgSubject to subject of msg
            set msgDate to date received of msg
            set msgContent to content of msg
            if length of msgContent > 300 then
                set msgContent to text 1 thru 300 of msgContent & "..."
            end if
            set output to output & "From: " & msgFrom & linefeed & "Subject: " & msgSubject & linefeed & "Date: " & (msgDate as string) & linefeed & msgContent & linefeed & "---" & linefeed
            set readCount to readCount + 1
            if readCount >= 10 then exit repeat
        end if
    end repeat
    if output is "" then
        return "No unread emails found."
    end if
    return output
end tell`;
          const { stdout } = await execAsync(
            `osascript -e '${escapeForShell(script)}'`,
            { timeout: 15000 },
          );
          return stdout.trim() || "No unread emails found.";
        }

        default:
          return `Error: Unknown action "${action}". Use "send", "check", or "read".`;
      }
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      return `Error interacting with Mail: ${msg}`;
    }
  },
};
