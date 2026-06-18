/**
 * StackOwl — Speech-to-Text Tool
 *
 * Transcribes audio files to text using the OpenAI Whisper API.
 */

import { exec } from "node:child_process";
import { log } from "../../logger.js";
import { promisify } from "node:util";
import { access, constants } from "node:fs/promises";
import { resolve, extname } from "node:path";
import type { ToolImplementation, ToolContext } from "../registry.js";

const execAsync = promisify(exec);
const EXEC_TIMEOUT_MS = 30_000;

const SUPPORTED_FORMATS = [
  ".mp3",
  ".mp4",
  ".wav",
  ".m4a",
  ".webm",
  ".mpeg",
  ".mpga",
  ".oga",
  ".ogg",
];

export const SpeechToTextTool: ToolImplementation = {
  definition: {
    name: "speech_to_text",
    description:
      "Transcribe audio files to text using OpenAI Whisper. Requires OPENAI_API_KEY. Supports mp3, mp4, wav, m4a.",
    parameters: {
      type: "object",
      properties: {
        file_path: {
          type: "string",
          description: "Path to the audio file to transcribe.",
        },
      },
      required: ["file_path"],
    },
    capabilities: ["speech_to_text", "audio_transcribe"],
    executionPolicy: { timeoutMs: 120_000, maxRetries: 1, retryDelayMs: 2_000 },
  },

  async execute(
    args: Record<string, unknown>,
    _context: ToolContext,
  ): Promise<string> {
    try {
      const filePath = args["file_path"] as string;
      if (!filePath) return "Error: 'file_path' parameter is required.";

      log.tool.debug("speech_to_text.execute: entry", { filePath });

      const apiKey = process.env["OPENAI_API_KEY"];
      if (!apiKey) {
        return (
          "OPENAI_API_KEY environment variable is not set. " +
          "To use speech-to-text, set your OpenAI API key:\n" +
          "  export OPENAI_API_KEY=sk-..."
        );
      }

      const resolvedPath = resolve(_context.cwd, filePath);

      // Check file exists
      try {
        await access(resolvedPath, constants.R_OK);
      } catch (err) {
        log.tool.warn('operation failed', err);
        return `Error: File not found or not readable: ${resolvedPath}`;
      }

      // Check supported format
      const ext = extname(resolvedPath).toLowerCase();
      if (!SUPPORTED_FORMATS.includes(ext)) {
        return `Error: Unsupported audio format '${ext}'. Supported formats: ${SUPPORTED_FORMATS.join(", ")}`;
      }

      // Use curl for multipart form upload (simpler than Node FormData with files)
      log.tool.debug("speech_to_text.execute: calling Whisper API", { resolvedPath });
      const { stdout, stderr } = await execAsync(
        `curl -s -X POST "https://api.openai.com/v1/audio/transcriptions" ` +
          `-H "Authorization: Bearer ${apiKey}" ` +
          `-F "file=@${resolvedPath}" ` +
          `-F "model=whisper-1"`,
        { timeout: EXEC_TIMEOUT_MS, cwd: _context.cwd },
      );

      if (stderr && !stdout) {
        return `Error calling Whisper API: ${stderr}`;
      }

      try {
        const result = JSON.parse(stdout) as {
          text?: string;
          error?: { message: string };
        };
        if (result.error) {
          log.tool.error("speech_to_text.execute: Whisper API error", new Error(result.error.message), { filePath });
          return `Whisper API error: ${result.error.message}`;
        }
        if (result.text) {
          log.tool.debug("speech_to_text.execute: exit", { success: true, transcriptLen: result.text.length });
          return `Transcription:\n\n${result.text}`;
        }
        return `Unexpected API response: ${stdout}`;
      } catch (err) {
        log.tool.warn('operation failed', err);
        return `Unexpected API response (not JSON): ${stdout}`;
      }
    } catch (error: any) {
      log.tool.error("speech_to_text.execute: unexpected error", error as Error, { filePath: args["file_path"] });
      return `Error transcribing audio: ${error.message ?? String(error)}`;
    }
  },
};
