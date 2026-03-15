import type { ToolImplementation, ToolContext } from "../registry.js";
import { exec } from "node:child_process";
import { promisify } from "node:util";

const execAsync = promisify(exec);

function escapeForShell(str: string): string {
    return str.replace(/'/g, "'\\''");
}

export const SpotlightSearchTool: ToolImplementation = {
    definition: {
        name: "spotlight_search",
        description:
            "Search the entire Mac using Spotlight (mdfind). Find files, folders, documents by content or name.",
        parameters: {
            type: "object",
            properties: {
                query: {
                    type: "string",
                    description: "Search query string.",
                },
                kind: {
                    type: "string",
                    description: "Optional file kind filter (e.g. 'pdf', 'image', 'folder', 'document', 'audio', 'video').",
                },
            },
            required: ["query"],
        },
    },

    async execute(args: Record<string, unknown>, _context: ToolContext): Promise<string> {
        const query = args.query as string;
        const kind = args.kind as string | undefined;

        if (!query) {
            return "Error: query parameter is required.";
        }

        try {
            let cmd: string;
            if (kind) {
                cmd = `mdfind 'kMDItemKind == "*${escapeForShell(kind)}*" && (kMDItemDisplayName == "*${escapeForShell(query)}*"cd || kMDItemTextContent == "*${escapeForShell(query)}*"cd)' | head -20`;
            } else {
                cmd = `mdfind '${escapeForShell(query)}' | head -20`;
            }

            const { stdout } = await execAsync(cmd, { timeout: 15000 });

            if (!stdout.trim()) {
                return `No results found for "${query}"${kind ? ` (kind: ${kind})` : ""}.`;
            }

            const results = stdout.trim().split("\n");
            return `Found ${results.length} result(s):\n${results.map((r, i) => `${i + 1}. ${r}`).join("\n")}`;
        } catch (error) {
            const msg = error instanceof Error ? error.message : String(error);
            return `Error running Spotlight search: ${msg}`;
        }
    },
};
