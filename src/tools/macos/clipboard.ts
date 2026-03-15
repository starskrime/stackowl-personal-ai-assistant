import type { ToolImplementation, ToolContext } from "../registry.js";
import { exec } from "node:child_process";
import { promisify } from "node:util";

const execAsync = promisify(exec);

function escapeForShell(str: string): string {
    return str.replace(/'/g, "'\\''");
}

function getClipboardCommands(): { read: string; write: (content: string) => string } | null {
    switch (process.platform) {
        case 'darwin':
            return {
                read: 'pbpaste',
                write: (content: string) => `echo '${escapeForShell(content)}' | pbcopy`,
            };
        case 'linux':
            // Try xclip first (most common), then xsel, then wl-copy (Wayland)
            return {
                read: 'xclip -selection clipboard -o 2>/dev/null || xsel --clipboard --output 2>/dev/null || wl-paste 2>/dev/null',
                write: (content: string) =>
                    `echo '${escapeForShell(content)}' | xclip -selection clipboard 2>/dev/null || ` +
                    `echo '${escapeForShell(content)}' | xsel --clipboard --input 2>/dev/null || ` +
                    `echo '${escapeForShell(content)}' | wl-copy 2>/dev/null`,
            };
        case 'win32':
            return {
                read: 'powershell -command "Get-Clipboard"',
                write: (content: string) => `powershell -command "Set-Clipboard -Value '${content.replace(/'/g, "''")}'"`
            };
        default:
            return null;
    }
}

export const ClipboardTool: ToolImplementation = {
    definition: {
        name: "clipboard",
        description:
            "Read from or write to the system clipboard. Works on macOS, Linux (X11/Wayland), and Windows.",
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
        const commands = getClipboardCommands();

        if (!commands) {
            return `Error: Clipboard not supported on platform "${process.platform}".`;
        }

        try {
            switch (action) {
                case "read": {
                    const { stdout } = await execAsync(commands.read, { timeout: 15000 });
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
                    await execAsync(commands.write(content), { timeout: 15000 });
                    return `Written to clipboard: ${content.length > 100 ? content.substring(0, 100) + "..." : content}`;
                }

                default:
                    return `Error: Unknown action "${action}". Use "read" or "write".`;
            }
        } catch (error) {
            const msg = error instanceof Error ? error.message : String(error);
            if (process.platform === 'linux') {
                return `Error with clipboard: ${msg}\nHint: Install xclip (apt install xclip) or xsel (apt install xsel) for clipboard access.`;
            }
            return `Error with clipboard: ${msg}`;
        }
    },
};
