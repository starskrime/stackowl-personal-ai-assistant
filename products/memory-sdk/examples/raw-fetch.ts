/**
 * Memory SDK — Raw Fetch Example (no Node.js dependency)
 *
 * Works in any environment that has fetch() — browser, Deno, edge functions, etc.
 * Talks to the Memory SDK REST server instead of using the SDK directly.
 */

const MEMORY_API = process.env.MEMORY_API_URL ?? "http://localhost:3002";

export async function memoryStore(
  userId: string,
  message: string,
  response: string,
): Promise<void> {
  await fetch(`${MEMORY_API}/memory/store`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ userId, message, response }),
  });
}

export async function memoryRecall(
  userId: string,
  query: string,
): Promise<{ facts: Array<{ fact: string; category: string }>; episodes: Array<{ summary: string }> }> {
  const res = await fetch(`${MEMORY_API}/memory/recall`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ userId, query }),
  });
  return res.json() as Promise<{ facts: Array<{ fact: string; category: string }>; episodes: Array<{ summary: string }> }>;
}

export async function memoryContext(
  userId: string,
  query?: string,
): Promise<string> {
  const res = await fetch(`${MEMORY_API}/memory/context`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ userId, query }),
  });
  const data = (await res.json()) as { contextString: string };
  return data.contextString;
}

// ─── Usage ────────────────────────────────────────────────────────────────────

/*
// In your chat handler (works in browser, Deno, Cloudflare Workers, etc.)
async function handleChat(userId: string, userMessage: string) {
  // 1. Get memory context
  const ctx = await memoryContext(userId, userMessage);

  // 2. Call your LLM (any provider)
  const assistantReply = await callYourLLM({
    systemPrompt: `You are a helpful assistant.\n\n${ctx}`,
    userMessage,
  });

  // 3. Store the exchange
  await memoryStore(userId, userMessage, assistantReply);

  return assistantReply;
}
*/
