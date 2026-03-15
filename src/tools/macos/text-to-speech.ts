import type { ToolImplementation, ToolContext } from "../registry.js";
import { exec } from "node:child_process";
import { promisify } from "node:util";

const execAsync = promisify(exec);

function escapeForShell(str: string): string {
    return str.replace(/'/g, "'\\''");
}

export const TextToSpeechTool: ToolImplementation = {
    definition: {
        name: "text_to_speech",
        description:
            "Speak text aloud using macOS text-to-speech. Great for reading content, announcements, or accessibility.",
        parameters: {
            type: "object",
            properties: {
                text: {
                    type: "string",
                    description: "The text to speak aloud.",
                },
                voice: {
                    type: "string",
                    description: "Optional voice name (e.g. 'Samantha', 'Alex', 'Victoria'). Uses system default if omitted.",
                },
            },
            required: ["text"],
        },
    },

    async execute(args: Record<string, unknown>, _context: ToolContext): Promise<string> {
        const text = args.text as string;
        const voice = args.voice as string | undefined;

        if (!text) {
            return "Error: text parameter is required.";
        }

        try {
            let cmd: string;
            if (voice) {
                cmd = `say -v '${escapeForShell(voice)}' '${escapeForShell(text)}'`;
            } else {
                cmd = `say '${escapeForShell(text)}'`;
            }

            await execAsync(cmd, { timeout: 15000 });
            return `Spoke: "${text.length > 100 ? text.substring(0, 100) + "..." : text}"${voice ? ` (voice: ${voice})` : ""}`;
        } catch (error) {
            const msg = error instanceof Error ? error.message : String(error);
            return `Error with text-to-speech: ${msg}`;
        }
    },
};
