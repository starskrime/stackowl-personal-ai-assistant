import type { ToolImplementation, ToolContext } from "../registry.js";
import { exec } from "node:child_process";
import { promisify } from "node:util";

const execAsync = promisify(exec);

function escapeForShell(str: string): string {
  return str.replace(/'/g, "'\\''");
}

export const AppleContactsTool: ToolImplementation = {
  definition: {
    name: "apple_contacts",
    description:
      "Search macOS Contacts for people by name, email, or phone number.",
    parameters: {
      type: "object",
      properties: {
        action: {
          type: "string",
          enum: ["search"],
          description: "Action to perform: search for a contact.",
        },
        query: {
          type: "string",
          description: "Name, email, or phone number to search for.",
        },
      },
      required: ["action", "query"],
    },
  },

  async execute(
    args: Record<string, unknown>,
    _context: ToolContext,
  ): Promise<string> {
    const action = args.action as string;
    const query = args.query as string;

    if (action !== "search") {
      return `Error: Unknown action "${action}". Use "search".`;
    }

    if (!query) {
      return "Error: 'search' action requires a query parameter.";
    }

    try {
      const script = `
tell application "Contacts"
    set searchTerm to "${escapeForShell(query)}"
    set output to ""
    set matchedPeople to (every person whose name contains searchTerm)

    if (count of matchedPeople) is 0 then
        set matchedPeople to (every person whose value of emails contains searchTerm)
    end if
    if (count of matchedPeople) is 0 then
        set matchedPeople to (every person whose value of phones contains searchTerm)
    end if

    repeat with p in matchedPeople
        set pName to name of p
        set output to output & "Name: " & pName & linefeed

        try
            set emailList to value of emails of p
            repeat with em in emailList
                set output to output & "  Email: " & em & linefeed
            end repeat
        end try

        try
            set phoneList to value of phones of p
            repeat with ph in phoneList
                set output to output & "  Phone: " & ph & linefeed
            end repeat
        end try

        try
            repeat with addr in addresses of p
                set addrStr to formatted address of addr
                set output to output & "  Address: " & addrStr & linefeed
            end repeat
        end try

        set output to output & "---" & linefeed
    end repeat

    if output is "" then
        return "No contacts found matching: " & searchTerm
    end if
    return output
end tell`;
      const { stdout } = await execAsync(
        `osascript -e '${escapeForShell(script)}'`,
        { timeout: 15000 },
      );
      return stdout.trim() || `No contacts found matching "${query}".`;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      return `Error searching Contacts: ${msg}`;
    }
  },
};
