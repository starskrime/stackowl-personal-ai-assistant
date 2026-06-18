import type { IntelligenceRouter } from "../intelligence/router.js";
import type { ProviderRegistry } from "../providers/registry.js";
import type { ChatMessage } from "../providers/base.js";
import type { FactCategory } from "../memory/db.js";

export interface ExtractedFact {
  fact: string;
  category: FactCategory;
}

const VALID_CATEGORIES = new Set<string>([
  "skill", "preference", "project_detail", "personal", "context",
  "goal", "habit", "relationship", "decision", "open_question",
  "active_goal", "sub_goal",
]);

const MAX_FACTS = 10;
const MAX_MESSAGES = 20;

export async function extractFactsFromConversation(
  messages: ChatMessage[],
  intelligence: IntelligenceRouter | undefined,
  providerRegistry: ProviderRegistry,
  fallbackProvider: string,
  fallbackModel: string,
): Promise<ExtractedFact[]> {
  const { provider: providerName, model } = intelligence?.resolve("extraction")
    ?? { provider: fallbackProvider, model: fallbackModel };

  let provider;
  try {
    provider = providerRegistry.get(providerName);
  } catch {
    return [];
  }

  const recent = messages.slice(-MAX_MESSAGES);
  const transcript = recent
    .map((m) => `${m.role}: ${m.content}`)
    .join("\n");

  const prompt = `You are analyzing a conversation to extract facts about the user.

Extract up to ${MAX_FACTS} concrete, specific facts about the user from this conversation.
Return ONLY a JSON array with no additional text.

Each fact must have:
- "fact": a concise statement about the user (e.g., "Prefers TypeScript over JavaScript")
- "category": one of: skill, preference, project_detail, personal, context, goal, habit, relationship, decision, open_question, active_goal, sub_goal

Only extract facts that are clearly stated or strongly implied. Do not invent facts.

Conversation:
${transcript}

Return only valid JSON like: [{"fact": "...", "category": "..."}]`;

  try {
    const response = await provider.chat(
      [{ role: "user", content: prompt }],
      model,
      { temperature: 0.2, maxTokens: 800 },
    );

    const text = response.content.trim();
    const jsonMatch = text.match(/\[[\s\S]*\]/);
    if (!jsonMatch) return [];

    const parsed = JSON.parse(jsonMatch[0]) as unknown[];
    if (!Array.isArray(parsed)) return [];

    return parsed
      .filter((item): item is { fact: string; category: string } =>
        typeof item === "object" &&
        item !== null &&
        typeof (item as Record<string, unknown>).fact === "string" &&
        typeof (item as Record<string, unknown>).category === "string" &&
        VALID_CATEGORIES.has((item as Record<string, unknown>).category as string),
      )
      .slice(0, MAX_FACTS)
      .map((item) => ({ fact: item.fact, category: item.category as FactCategory }));
  } catch {
    return [];
  }
}
