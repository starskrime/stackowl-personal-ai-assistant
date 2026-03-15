import type { ToolImplementation, ToolContext } from "../registry.js";
import { exec } from "node:child_process";
import { promisify } from "node:util";

const execAsync = promisify(exec);

function escapeForShell(str: string): string {
    return str.replace(/'/g, "'\\''");
}

export const ClipboardTool: ToolImplementation = {
    definition: {
        name: "clipboard",
        description:
            "Read from or write to the macOS clipboard (pasteboard).",
        parameters: {
            type: "object",
            properties: {
                action: {
                    type: "string",
                    enum: ["read", "write"],
                    description: "Action to perform: read from or write to the clipboard.",
                },
                content: {
                    type: "string",
                    description: "Content to write to the clipboard (required for 'write').",
                },
            },
            required: ["action"],
        },
    },

    async execute(args: Record<string, unknown>, _context: ToolContext): Promise<string> {
        const action = args.action as string;

        try {
            switch (action) {
                case "read": {
                    const { stdout } = await execAsync("pbpaste", { timeout: 15000 });
                    if (!stdout) {
                        return "Clipboard is empty.";
                    }
                    return `Clipboard contents:\n${stdout}`;
                }

                case "write": {
                    const content = args.content as string;
                    if (!content) {
                        return "Error: 'write' action requires a content parameter.";
                    }
                    await execAsync(`echo '${escapeForShell(content)}' | pbcopy`, { timeout: 15000 });
                    return `Written to clipboard: ${content.length > 100 ? content.substring(0, 100) + "..." : content}`;
                }

                default:
                    return `Error: Unknown action "${action}". Use "read" or "write".`;
            }
        } catch (error) {
            const msg = error instanceof Error ? error.message : String(error);
            return `Error with clipboard: ${msg}`;
        }
    },
};
