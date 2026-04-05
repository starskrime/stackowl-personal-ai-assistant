/**
 * Memory SDK — Express Middleware Example
 *
 * Drop this into any Express app to give your AI assistant persistent memory.
 * Attaches `req.memory` to every request so your route handlers can call
 * store(), recall(), and context() with zero setup.
 */

import type { Request, Response, NextFunction } from "express";
import { MemorySDK } from "../index.js";
import type { MemoryProvider } from "../index.js";

declare global {
  namespace Express {
    interface Request {
      memory: {
        store(message: string, response: string): Promise<void>;
        recall(query: string): Promise<import("../types.js").RecallResult>;
        context(query?: string): Promise<string>;
      };
    }
  }
}

export function memoryMiddleware(sdk: MemorySDK, getUserId: (req: Request) => string) {
  return (req: Request, _res: Response, next: NextFunction) => {
    const userId = getUserId(req);

    req.memory = {
      async store(message, response) {
        await sdk.store(userId, message, response);
      },
      async recall(query) {
        return sdk.recall(userId, query);
      },
      async context(query) {
        const result = await sdk.context(userId, query);
        return result.contextString;
      },
    };

    next();
  };
}

// ─── Usage Example ────────────────────────────────────────────────────────────

/*
import express from "express";
import { MemorySDK } from "@stackowl/memory-sdk";
import { AnthropicMemoryAdapter } from "@stackowl/memory-sdk/adapters/anthropic";
import Anthropic from "@anthropic-ai/sdk";
import { memoryMiddleware } from "@stackowl/memory-sdk/examples/express-middleware";

const anthropic = new Anthropic();
const provider = new AnthropicMemoryAdapter(anthropic);
const sdk = new MemorySDK({ workspacePath: "./memory", provider });

const app = express();
app.use(express.json());

// Attach memory to all requests
app.use(memoryMiddleware(sdk, (req) => req.headers["x-user-id"] as string ?? "anonymous"));

app.post("/chat", async (req, res) => {
  const { message } = req.body;

  // Get enriched context for this user
  const memoryContext = await req.memory.context(message);

  // Call your LLM with memory context injected
  const response = await anthropic.messages.create({
    model: "claude-haiku-4-5-20251001",
    max_tokens: 1024,
    system: `You are a helpful assistant.\n\n${memoryContext}`,
    messages: [{ role: "user", content: message }],
  });

  const assistantMessage = response.content[0].type === "text"
    ? response.content[0].text
    : "";

  // Store this exchange in memory
  await req.memory.store(message, assistantMessage);

  res.json({ message: assistantMessage });
});

app.listen(3000);
*/
