/**
 * StackOwl — Fact Extractor
 *
 * LLM-powered extraction of structured facts from conversation transcripts.
 * Inspired by mem0's fact extraction pipeline but adapted for StackOwl's
 * domain (CLI/assistant workflows, code, projects, preferences).
 *
 * Extraction categories:
 *   - preference: User likes/dislikes, communication style, formatting prefs
 *   - project_detail: Project names, tech stacks, architecture decisions
 *   - skill: Tools/skills the user has or is learning
 *   - goal: What the user is trying to accomplish
 *   - personal: Name, location, timezone, background
 *   - habit: Patterns of behavior (active hours, preferred tools)
 *   - relationship: How user relates to people/projects mentioned
 *   - context: Environment, setup, current state
 *
 * Each extracted fact includes:
 *   - The fact string (natural language)
 *   - Entity (the subject the fact is about)
 *   - Category (one of the 8 types above)
 *   - Confidence (0-1, LLM-assessed extraction confidence)
 *   - Source (always "inferred" from extractor)
 *   - TTL (default from config, can be overridden per-category)
 */

import type { ModelProvider } from "../providers/base.js";
import type { ChatMessage } from "../providers/base.js";
import type { FactCategory, StoredFact } from "./fact-store.js";
import { log } from "../logger.js";

// ─── Types ─────────────────────────────────────────────────────

export interface ExtractedFact {
  fact: string;
  entity?: string;
  category: FactCategory;
  confidence: number;
}

export interface ExtractionResult {
  facts: ExtractedFact[];
  summary: string;
  dominantSentiment: "positive" | "neutral" | "negative";
}

export interface FactExtractorConfig {
  maxFactsPerSession: number;
  minConfidenceThreshold: number;
  defaultTtlDays: number;
  categories: FactCategory[];
}

// ─── Constants ────────────────────────────────────────────────

const DEFAULT_EXTRACTOR_CONFIG: FactExtractorConfig = {
  maxFactsPerSession: 10,
  minConfidenceThreshold: 0.4,
  defaultTtlDays: 30,
  categories: [
    "preference",
    "project_detail",
    "skill",
    "goal",
    "personal",
    "habit",
    "relationship",
    "context",
  ],
};

/** Input type for FactStore.add() — all fields except auto-managed ones */
export type StoredFactInput = Omit<
  StoredFact,
  "id" | "createdAt" | "updatedAt" | "accessCount"
>;

// ─── Fact Extractor ──────────────────────────────────────────

export class FactExtractor {
  private config: FactExtractorConfig;

  constructor(
    private provider: ModelProvider,
    config: Partial<FactExtractorConfig> = {},
  ) {
    this.config = { ...DEFAULT_EXTRACTOR_CONFIG, ...config };
  }

  /**
   * Extract structured facts from a conversation transcript.
   * Called after session ends (or every N messages for long sessions).
   *
   * @param messages Conversation messages (user + assistant)
   * @param userId User identifier for fact ownership
   * @returns Structured facts ready for FactStore.addBatch()
   */
  async extract(
    messages: ChatMessage[],
    userId: string,
  ): Promise<StoredFactInput[]> {
    const relevant = messages.filter(
      (m) => m.role === "user" || m.role === "assistant",
    );
    if (relevant.length < 2) return [];

    const transcript = relevant
      .slice(-30)
      .map(
        (m) => `[${m.role.toUpperCase()}]: ${(m.content ?? "").slice(0, 300)}`,
      )
      .join("\n");

    const categoryList = this.config.categories.join(", ");

    const prompt =
      `You are a fact extraction module for a personal AI assistant.\n` +
      `Extract IMPORTANT factual statements from this conversation.\n` +
      `Focus on things worth remembering across sessions:\n` +
      `- User preferences and dislikes\n` +
      `- Project details, tech stack, architecture\n` +
      `- Skills the user has or is developing\n` +
      `- Goals and intentions the user expressed\n` +
      `- Personal context (name, location, background)\n` +
      `- Behavioral patterns (active hours, preferred tools)\n` +
      `- Relationships to people/projects mentioned\n` +
      `- Current setup or environment\n\n` +
      `Categories: ${categoryList}\n\n` +
      `Return a JSON object with:\n` +
      `- "facts": array of {fact, entity, category, confidence} — max ${this.config.maxFactsPerSession}\n` +
      `- "summary": 1-sentence summary of what happened\n` +
      `- "dominantSentiment": "positive", "neutral", or "negative"\n\n` +
      `Rules:\n` +
      `- Only extract facts with confidence >= ${this.config.minConfidenceThreshold}\n` +
      `- Facts should be 3-15 words each\n` +
      `- Entity is the subject (person, project, tool, or "user" if about them)\n` +
      `- Confidence: 0.7-0.9 for clear explicit statements, 0.4-0.7 for strong inferences\n` +
      `- Do NOT extract: small talk, jokes, obvious things, transient states\n\n` +
      `CONVERSATION:\n${transcript}\n\n` +
      `Return ONLY valid JSON.`;

    try {
      const response = await this.provider.chat(
        [
          {
            role: "system",
            content:
              "You are a precise fact extraction system. Output only valid JSON.",
          },
          { role: "user", content: prompt },
        ],
        undefined,
        { temperature: 0.2, maxTokens: 1200 },
      );

      const text = response.content.trim();
      const match = text.match(/\{[\s\S]*\}/);
      if (!match) {
        log.engine.warn("[FactExtractor] No JSON found in response");
        return [];
      }

      const parsed = JSON.parse(match[0]) as {
        facts?: Array<{
          fact?: string;
          entity?: string;
          category?: string;
          confidence?: number;
        }>;
        summary?: string;
        dominantSentiment?: string;
      };

      if (!parsed.facts || !Array.isArray(parsed.facts)) {
        return [];
      }

      const ttlMs = this.config.defaultTtlDays * 24 * 60 * 60 * 1000;
      const expiresAt = new Date(Date.now() + ttlMs).toISOString();

      type StoredFactInput = Omit<
        StoredFact,
        "id" | "createdAt" | "updatedAt" | "accessCount"
      >;
      const storedFacts: StoredFactInput[] = [];

      for (const f of parsed.facts) {
        if (!f.fact || typeof f.fact !== "string") continue;
        const category = this.normalizeCategory(f.category);
        if (!category) continue;

        const confidence = Math.max(0, Math.min(1, f.confidence ?? 0.5));
        if (confidence < this.config.minConfidenceThreshold) continue;

        const factText = f.fact.trim();
        if (factText.length < 5 || factText.length > 200) continue;

        storedFacts.push({
          userId,
          fact: factText,
          entity: f.entity?.trim() || undefined,
          category,
          confidence,
          source: "inferred",
          expiresAt,
        });
      }

      log.engine.debug(
        `[FactExtractor] Extracted ${storedFacts.length} facts from ${relevant.length} messages`,
      );
      return storedFacts;
    } catch (err) {
      log.engine.warn(
        `[FactExtractor] Extraction failed: ${err instanceof Error ? err.message : err}`,
      );
      return [];
    }
  }

  /**
   * Extract facts from a single user message (for real-time extraction).
   * Less verbose than full session extraction.
   */
  async extractFromMessage(
    message: string,
    userId: string,
  ): Promise<StoredFactInput[]> {
    if (message.trim().length < 20) return [];

    const categoryList = this.config.categories.join(", ");

    const prompt =
      `Extract factual statements from this user message.\n` +
      `Categories: ${categoryList}\n\n` +
      `Return a JSON array of {fact, entity, category, confidence}.\n` +
      `Only extract if the message contains a clear fact worth remembering.\n` +
      `Max 3 facts. Confidence: 0.7+ for explicit, 0.4-0.7 for implied.\n\n` +
      `Message: ${message.slice(0, 500)}\n\n` +
      `Return ONLY valid JSON array.`;

    try {
      const response = await this.provider.chat(
        [
          {
            role: "system",
            content:
              "You are a precise fact extraction system. Output only valid JSON.",
          },
          { role: "user", content: prompt },
        ],
        undefined,
        { temperature: 0.1, maxTokens: 400 },
      );

      const text = response.content.trim();
      const match = text.match(/\[[\s\S]*\]|\{[\s\S]*\}/);
      if (!match) return [];

      const parsed = JSON.parse(match[0]);
      const facts = Array.isArray(parsed) ? parsed : (parsed.facts ?? []);

      const ttlMs = this.config.defaultTtlDays * 24 * 60 * 60 * 1000;
      const expiresAt = new Date(Date.now() + ttlMs).toISOString();

      const storedFacts: StoredFactInput[] = [];

      for (const f of facts.slice(0, 3)) {
        if (!f.fact || typeof f.fact !== "string") continue;
        const category = this.normalizeCategory(f.category);
        if (!category) continue;

        const confidence = Math.max(0, Math.min(1, f.confidence ?? 0.5));
        if (confidence < this.config.minConfidenceThreshold) continue;

        const factText = f.fact.trim();
        if (factText.length < 5) continue;

        storedFacts.push({
          userId,
          fact: factText,
          entity: f.entity?.trim() || undefined,
          category,
          confidence,
          source: "inferred",
          expiresAt,
        });
      }

      return storedFacts;
    } catch (err) {
      log.engine.warn(
        `[FactExtractor] Single-message extraction failed: ${err instanceof Error ? err.message : err}`,
      );
      return [];
    }
  }

  /**
   * Validate and normalize a category string.
   */
  private normalizeCategory(cat?: string): FactCategory | null {
    if (!cat || typeof cat !== "string") return null;
    const lower = cat.toLowerCase().trim();
    const valid: FactCategory[] = [
      "preference",
      "project_detail",
      "personal",
      "skill",
      "goal",
      "relationship",
      "habit",
      "context",
    ];
    if (valid.includes(lower as FactCategory)) return lower as FactCategory;
    if (lower.includes("prefer")) return "preference";
    if (
      lower.includes("project") ||
      lower.includes("tech") ||
      lower.includes("stack")
    )
      return "project_detail";
    if (lower.includes("skill") || lower.includes("learn")) return "skill";
    if (
      lower.includes("goal") ||
      lower.includes("want") ||
      lower.includes("need")
    )
      return "goal";
    if (
      lower.includes("personal") ||
      lower.includes("name") ||
      lower.includes("live")
    )
      return "personal";
    if (
      lower.includes("habit") ||
      lower.includes("always") ||
      lower.includes("usually")
    )
      return "habit";
    if (
      lower.includes("relat") ||
      lower.includes("friend") ||
      lower.includes("colleague")
    )
      return "relationship";
    return "context";
  }
}
