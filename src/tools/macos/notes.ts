import type { ToolImplementation, ToolContext } from "../registry.js";
import { exec } from "node:child_process";
import { promisify } from "node:util";

const execAsync = promisify(exec);

function escapeForShell(str: string): string {
    return str.replace(/'/g, "'\\''");
}

export const AppleNotesTool: ToolImplementation = {
    definition: {
        name: "apple_notes",
        description:
            "Manage macOS Notes — list, search, or create notes.",
        parameters: {
            type: "object",
            properties: {
                action: {
                    type: "string",
                    enum: ["list", "search", "create"],
                    description: "Action to perform: list recent notes, search by keyword, or create a new note.",
                },
                keyword: {
                    type: "string",
                    description: "Search keyword (required for 'search').",
                },
                title: {
                    type: "string",
                    description: "Note title (required for 'create').",
                },
                body: {
                    type: "string",
                    description: "Note body content (required for 'create').",
                },
            },
            required: ["action"],
        },
    },

    async execute(args: Record<string, unknown>, _context: ToolContext): Promise<string> {
        const action = args.action as string;

        try {
            switch (action) {
                case "list": {
                    const script = `
tell application "Notes"
    set output to ""
    set noteList to every note of default account
    set noteCount to count of noteList
    if noteCount > 20 then set noteCount to 20
    repeat with i from 1 to noteCount
        set n to item i of noteList
        set nName to name of n
        set nDate to modification date of n
        set output to output & nName & " | Modified: " & (nDate as string) & linefeed
    end repeat
    if output is "" then
        return "No notes found."
    end if
    return output
end tell`;
                    const { stdout } = await execAsync(`osascript -e '${escapeForShell(script)}'`, { timeout: 15000 });
                    return stdout.trim() || "No notes found.";
                }

                case "search": {
                    const keyword = args.keyword as string;
                    if (!keyword) {
                        return "Error: 'search' action requires a keyword parameter.";
                    }

                    const script = `
tell application "Notes"
    set output to ""
    set searchTerm to "${escapeForShell(keyword)}"
    set matchedNotes to (every note of default account whose name contains searchTerm)
    repeat with n in matchedNotes
        set nName to name of n
        set nDate to modification date of n
        set nBody to plaintext of n
        if length of nBody > 200 then
            set nBody to text 1 thru 200 of nBody & "..."
        end if
        set output to output & nName & " | Modified: " & (nDate as string) & linefeed & nBody & linefeed & "---" & linefeed
    end repeat
    if output is "" then
        return "No notes found matching: " & searchTerm
    end if
    return output
end tell`;
                    const { stdout } = await execAsync(`osascript -e '${escapeForShell(script)}'`, { timeout: 15000 });
                    return stdout.trim() || `No notes found matching "${keyword}".`;
                }

                case "create": {
                    const title = args.title as string;
                    const body = args.body as string;
                    if (!title || !body) {
                        return "Error: 'create' action requires title and body parameters.";
                    }

                    const script = `
tell application "Notes"
    set noteBody to "<h1>${escapeForShell(title)}</h1><br>" & "${escapeForShell(body)}"
    make new note at default account with properties {name:"${escapeForShell(title)}", body:noteBody}
    return "Note created: ${escapeForShell(title)}"
end tell`;
                    const { stdout } = await execAsync(`osascript -e '${escapeForShell(script)}'`, { timeout: 15000 });
                    return stdout.trim() || `Note "${title}" created successfully.`;
                }

                default:
                    return `Error: Unknown action "${action}". Use "list", "search", or "create".`;
            }
        } catch (error) {
            const msg = error instanceof Error ? error.message : String(error);
            return `Error interacting with Notes: ${msg}`;
        }
    },
};
