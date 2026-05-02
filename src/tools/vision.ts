/**
 * StackOwl — Vision Tool
 *
 * Analyzes image files using a multimodal vision-capable provider.
 * Routes to the appropriate model via IntelligenceRouter and returns a
 * structured description of image contents, detected objects, and any
 * text found in the image.
 */

import { readFile } from "node:fs/promises";
import type { ToolImplementation, ToolContext } from "./registry.js";

export interface VisionResult {
  description: string;
  objects: string[];
  text?: string | null;
}

export const VisionTool: ToolImplementation = {
  definition: {
    name: "vision",
    description:
      "Analyze an image file and answer a question about it. Returns a structured description, " +
      "list of detected objects, and any text found in the image. " +
      "Requires a vision-capable provider (e.g. Anthropic claude-opus-4-5). " +
      'Example: vision(imagePath: "/tmp/screenshot.png", question: "What error is shown?")',
    parameters: {
      type: "object",
      properties: {
        imagePath: {
          type: "string",
          description: "Absolute path to the image file (PNG, JPG, GIF, WEBP).",
        },
        question: {
          type: "string",
          description: "What do you want to know about the image?",
        },
      },
      required: ["imagePath", "question"],
    },
    capabilities: ["vision", "multimodal"],
  },

  category: "cognitive",
  source: "builtin",

  async execute(args: Record<string, unknown>, context: ToolContext): Promise<string> {
    const imagePath = args["imagePath"] as string;
    const question  = args["question"]  as string;

    if (!imagePath) {
      return JSON.stringify({
        success: false,
        error: { code: "MISSING_ARG", message: "imagePath is required" },
      });
    }
    if (!question) {
      return JSON.stringify({
        success: false,
        error: { code: "MISSING_ARG", message: "question is required" },
      });
    }

    // Require providerRegistry in engineContext
    const engineContext = (context as unknown as { engineContext?: Record<string, unknown> }).engineContext;
    const providerRegistry = engineContext?.["providerRegistry"] as
      | { get(name?: string): import("../providers/base.js").ModelProvider }
      | undefined;

    if (!providerRegistry) {
      return JSON.stringify({
        success: false,
        error: { code: "NO_PROVIDER", message: "Provider registry unavailable." },
      });
    }

    // Route to vision-capable model via IntelligenceRouter when available
    const intelligenceRouter = engineContext?.["intelligenceRouter"] as
      | { resolve(taskType: string): { provider: string; model: string } }
      | undefined;

    const resolved = intelligenceRouter?.resolve?.("conversation") ?? {
      provider: undefined,
      model: undefined,
    };

    let provider: import("../providers/base.js").ModelProvider;
    try {
      provider = providerRegistry.get(resolved.provider);
    } catch {
      return JSON.stringify({
        success: false,
        error: {
          code: "PROVIDER_NOT_FOUND",
          message: `Provider "${resolved.provider ?? "default"}" not found.`,
        },
      });
    }

    // Read image as base64
    let imageBuffer: Buffer;
    try {
      imageBuffer = await readFile(imagePath);
    } catch {
      return JSON.stringify({
        success: false,
        error: { code: "FILE_NOT_FOUND", message: `Cannot read image: ${imagePath}` },
      });
    }

    const base64Image = imageBuffer.toString("base64");
    const ext = imagePath.split(".").pop()?.toLowerCase() ?? "jpeg";
    const mediaTypeMap: Record<string, string> = {
      jpg:  "image/jpeg",
      jpeg: "image/jpeg",
      png:  "image/png",
      gif:  "image/gif",
      webp: "image/webp",
    };
    const mediaType = mediaTypeMap[ext] ?? "image/jpeg";

    const systemPrompt =
      "You are a vision analysis assistant. Respond ONLY with valid JSON: " +
      '{ "description": "string", "objects": ["string"], "text": "string or null" }';

    const response = await provider.chat(
      [
        {
          role: "user" as const,
          content: JSON.stringify([
            {
              type: "image",
              source: { type: "base64", media_type: mediaType, data: base64Image },
            },
            { type: "text", text: question },
          ]),
        },
      ],
      resolved.model,
      { raw: { system: systemPrompt } },
    );

    let result: VisionResult;
    try {
      result = JSON.parse(response.content) as VisionResult;
    } catch {
      result = { description: response.content, objects: [], text: null };
    }

    return JSON.stringify({ success: true, data: result });
  },
};
