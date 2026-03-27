import type { ToolImplementation, ToolContext } from "../registry.js";
import { exec } from "node:child_process";
import { promisify } from "node:util";

const execAsync = promisify(exec);

function escapeForShell(str: string): string {
  return str.replace(/'/g, "'\\''");
}

function getTTSCommand(text: string, voice?: string): string | null {
  const escaped = escapeForShell(text);

  switch (process.platform) {
    case "darwin":
      return voice
        ? `say -v '${escapeForShell(voice)}' '${escaped}'`
        : `say '${escaped}'`;
    case "linux":
      // Try espeak-ng first (modern), then espeak (legacy), then spd-say (speech-dispatcher)
      if (voice) {
        return `espeak-ng -v '${escapeForShell(voice)}' '${escaped}' 2>/dev/null || espeak -v '${escapeForShell(voice)}' '${escaped}' 2>/dev/null || spd-say '${escaped}' 2>/dev/null`;
      }
      return `espeak-ng '${escaped}' 2>/dev/null || espeak '${escaped}' 2>/dev/null || spd-say '${escaped}' 2>/dev/null`;
    case "win32":
      // PowerShell SpeechSynthesizer
      return `powershell -command "Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; $s.Speak('${text.replace(/'/g, "''")}')"`;
    default:
      return null;
  }
}

export const TextToSpeechTool: ToolImplementation = {
  definition: {
    name: "text_to_speech",
    description:
      "Speak text aloud using system text-to-speech. Works on macOS (say), Linux (espeak/espeak-ng), and Windows (SAPI).",
    parameters: {
      type: "object",
      properties: {
        text: {
          type: "string",
          description: "The text to speak aloud.",
        },
        voice: {
          type: "string",
          description:
            "Optional voice name. macOS: 'Samantha', 'Alex'. Linux: 'en', 'en-us'. Uses system default if omitted.",
        },
      },
      required: ["text"],
    },
  },

  async execute(
    args: Record<string, unknown>,
    _context: ToolContext,
  ): Promise<string> {
    const text = args.text as string;
    const voice = args.voice as string | undefined;

    if (!text) {
      return "Error: text parameter is required.";
    }

    const cmd = getTTSCommand(text, voice);
    if (!cmd) {
      return `Error: Text-to-speech not supported on platform "${process.platform}".`;
    }

    try {
      await execAsync(cmd, { timeout: 30000 });
      return `Spoke: "${text.length > 100 ? text.substring(0, 100) + "..." : text}"${voice ? ` (voice: ${voice})` : ""}`;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      if (process.platform === "linux") {
        return `Error with text-to-speech: ${msg}\nHint: Install espeak-ng (apt install espeak-ng) for TTS support.`;
      }
      return `Error with text-to-speech: ${msg}`;
    }
  },
};
