/**
 * StackOwl — Image Generation Tool
 *
 * Generates images from text descriptions using the OpenAI DALL-E API.
 */

import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import type { ToolImplementation, ToolContext } from "../registry.js";

export const ImageGenerationTool: ToolImplementation = {
  definition: {
    name: "image_generation",
    description:
      "Generate images from text descriptions using AI (DALL-E). Requires OPENAI_API_KEY environment variable.",
    parameters: {
      type: "object",
      properties: {
        prompt: {
          type: "string",
          description: "Text description of the image to generate.",
        },
        size: {
          type: "string",
          description:
            'Image size. One of "256x256", "512x512", "1024x1024". Defaults to "1024x1024".',
        },
        provider: {
          type: "string",
          description:
            'Image generation provider. Currently only "openai" is supported.',
        },
      },
      required: ["prompt"],
    },
  },

  async execute(
    args: Record<string, unknown>,
    _context: ToolContext,
  ): Promise<string> {
    try {
      const prompt = args["prompt"] as string;
      if (!prompt) return "Error: 'prompt' parameter is required.";

      const size = (args["size"] as string) || "1024x1024";
      const validSizes = ["256x256", "512x512", "1024x1024"];
      if (!validSizes.includes(size)) {
        return `Error: Invalid size '${size}'. Must be one of: ${validSizes.join(", ")}`;
      }

      const apiKey = process.env["OPENAI_API_KEY"];
      if (!apiKey) {
        return (
          "OPENAI_API_KEY environment variable is not set. " +
          "To use image generation, set your OpenAI API key:\n" +
          "  export OPENAI_API_KEY=sk-..."
        );
      }

      const response = await fetch(
        "https://api.openai.com/v1/images/generations",
        {
          method: "POST",
          headers: {
            Authorization: `Bearer ${apiKey}`,
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            model: "dall-e-3",
            prompt,
            n: 1,
            size,
          }),
          signal: AbortSignal.timeout(30_000),
        },
      );

      if (!response.ok) {
        const errorText = await response.text();
        return `OpenAI API error (HTTP ${response.status}): ${errorText}`;
      }

      const result = (await response.json()) as {
        data: Array<{ url: string; revised_prompt?: string }>;
      };

      const imageUrl = result.data?.[0]?.url;
      if (!imageUrl) return "Error: No image URL returned from API.";

      // Download the image
      const imagesDir = resolve(_context.cwd, "workspace", "images");
      await mkdir(imagesDir, { recursive: true });

      const filename = `image-${Date.now()}.png`;
      const imagePath = resolve(imagesDir, filename);

      const imageResponse = await fetch(imageUrl, {
        signal: AbortSignal.timeout(30_000),
      });
      if (!imageResponse.ok) {
        return `Image generated but download failed (HTTP ${imageResponse.status}). URL: ${imageUrl}`;
      }

      const buffer = Buffer.from(await imageResponse.arrayBuffer());
      await writeFile(imagePath, buffer);

      const revisedPrompt = result.data[0]?.revised_prompt;
      let output = `Image generated and saved to: ${imagePath}`;
      if (revisedPrompt) {
        output += `\nRevised prompt: ${revisedPrompt}`;
      }
      return output;
    } catch (error: any) {
      return `Error generating image: ${error.message ?? String(error)}`;
    }
  },
};
