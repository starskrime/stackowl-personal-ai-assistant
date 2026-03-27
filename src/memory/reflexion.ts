/**
 * StackOwl — Memory Reflexion Engine
 * Replaces append-only MemoryConsolidator with structured memory.
 */

import { readFile, writeFile, mkdir } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join } from "node:path";
import type { ChatMessage, ModelProvider } from "../providers/base.js";
import type { OwlInstance } from "../owls/persona.js";
import { log } from "../logger.js";

export type MemoryCategory =
  | "preference"
  | "fact"
  | "decision"
  | "project"
  | "context";
export type MemorySource = "conversation" | "extraction" | "reflexion" | "user";

export interface MemoryEntry {
  id: string;
  content: string;
  category: MemoryCategory;
  source: MemorySource;
  createdAt: string;
  lastAccessedAt: string;
  accessCount: number;
  importance: number;
  confidence: number;
  relatedTopics: string[];
  sessionId?: string;
  owlName?: string;
  compressed?: boolean;
  originalContent?: string;
}

export interface MemoryStore {
  entries: Record<string, MemoryEntry>;
  byCategory: Record<MemoryCategory, string[]>;
  byTopic: Record<string, string[]>;
  lastReflexed: string;
  lastConsolidated: string;
  totalEntries: number;
  archiveCount: number;
}

export interface ReflexionResult {
  newEntries: MemoryEntry[];
  compressedEntries: string[];
  revivedEntries: string[];
  insights: string[];
  memoryHealth: number;
  durationMs: number;
}

export interface ConsolidationResult {
  entriesAdded: number;
  entriesUpdated: number;
  duplicatesSkipped: number;
}

const MEMORY_DIR = "memory";
const INDEX_FILE = "index.json";
const ARCHIVE_DIR = "archive";
const MAX_ENTRIES = 500;
const COMPRESSION_THRESHOLD = 100;
const ARCHIVE_BATCH_SIZE = 50;
const REVIVAL_CHECK_INTERVAL_DAYS = 7;

function generateId(): string {
  return `mem_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
}

function calculateImportance(entry: MemoryEntry): number {
  let score = 0.3;
  score += Math.min(0.3, entry.accessCount * 0.05);
  const ageDays =
    (Date.now() - new Date(entry.createdAt).getTime()) / (1000 * 60 * 60 * 24);
  if (ageDays < 3) score += 0.2;
  else if (ageDays < 7) score += 0.1;
  else if (ageDays < 30) score += 0.05;
  score += entry.confidence * 0.2;
  return Math.min(1.0, score);
}

function categorizeFact(content: string): MemoryCategory {
  const lower = content.toLowerCase();
  if (/prefer|like|hate|want|dislike|favorite|better|instead/i.test(lower))
    return "preference";
  if (/decided|going to|will use|chose|choosing|picking/i.test(lower))
    return "decision";
  if (/project|repo|codebase|building|working on|implementing/i.test(lower))
    return "project";
  if (/remember|note|important|fact|truth|always|never/i.test(lower))
    return "fact";
  return "context";
}

function extractTopics(content: string): string[] {
  const techTerms = [
    "typescript",
    "javascript",
    "python",
    "rust",
    "golang",
    "java",
    "swift",
    "react",
    "vue",
    "angular",
    "nextjs",
    "node",
    "deno",
    "docker",
    "kubernetes",
    "k8s",
    "aws",
    "gcp",
    "azure",
    "postgresql",
    "mysql",
    "mongodb",
    "redis",
    "openai",
    "anthropic",
    "claude",
    "ollama",
    "llm",
    "git",
    "github",
    "gitlab",
    "api",
    "rest",
    "graphql",
    "grpc",
    "test",
    "testing",
    "ci",
    "cd",
    "security",
    "auth",
    "oauth",
    "jwt",
    "web",
    "mobile",
    "frontend",
    "backend",
    "fullstack",
    "macos",
    "linux",
    "windows",
    "ios",
    "android",
  ];
  const found = new Set<string>();
  const lower = content.toLowerCase();
  for (const term of techTerms) {
    if (lower.includes(term)) found.add(term);
  }
  return [...found].slice(0, 5);
}

export class MemoryReflexionEngine {
  private memoryDir: string;
  private indexPath: string;
  private archiveDir: string;
  private store: MemoryStore;
  private provider?: ModelProvider;
  private owl?: OwlInstance;

  constructor(
    workspacePath: string,
    provider?: ModelProvider,
    owl?: OwlInstance,
  ) {
    this.memoryDir = join(workspacePath, MEMORY_DIR);
    this.indexPath = join(this.memoryDir, INDEX_FILE);
    this.archiveDir = join(this.memoryDir, ARCHIVE_DIR);
    this.provider = provider;
    this.owl = owl;

    this.store = {
      entries: {},
      byCategory: {
        preference: [],
        fact: [],
        decision: [],
        project: [],
        context: [],
      },
      byTopic: {},
      lastReflexed: "",
      lastConsolidated: "",
      totalEntries: 0,
      archiveCount: 0,
    };
  }

  async init(): Promise<void> {
    if (!existsSync(this.memoryDir))
      await mkdir(this.memoryDir, { recursive: true });
    if (!existsSync(this.archiveDir))
      await mkdir(this.archiveDir, { recursive: true });
    await this.load();
  }

  private async load(): Promise<void> {
    if (!existsSync(this.indexPath)) return;
    try {
      const raw = await readFile(this.indexPath, "utf-8");
      this.store = JSON.parse(raw);
      for (const entry of Object.values(this.store.entries)) {
        entry.importance = calculateImportance(entry);
      }
    } catch (err) {
      log.memory.warn(`[MemoryReflexion] Failed to load: ${err}`);
    }
  }

  private async save(): Promise<void> {
    this.store.totalEntries = Object.keys(this.store.entries).length;
    await writeFile(
      this.indexPath,
      JSON.stringify(this.store, null, 2),
      "utf-8",
    );
  }

  async consolidate(
    messages: ChatMessage[],
    sessionId?: string,
  ): Promise<ConsolidationResult> {
    await this.init();

    const relevant = messages.filter(
      (m) => m.role === "user" || m.role === "assistant",
    );
    if (relevant.length < 4) {
      return { entriesAdded: 0, entriesUpdated: 0, duplicatesSkipped: 0 };
    }

    const transcript = relevant
      .slice(-20)
      .map(
        (m) => `[${m.role.toUpperCase()}]: ${(m.content ?? "").slice(0, 300)}`,
      )
      .join("\n\n");

    const result: ConsolidationResult = {
      entriesAdded: 0,
      entriesUpdated: 0,
      duplicatesSkipped: 0,
    };

    if (this.provider) {
      try {
        const facts = await this.extractFactsWithLLM(transcript);
        for (const fact of facts) {
          const existing = this.findSimilar(fact.content);
          if (existing) {
            existing.accessCount++;
            existing.lastAccessedAt = new Date().toISOString();
            existing.importance = calculateImportance(existing);
            result.duplicatesSkipped++;
          } else {
            const entry = this.createEntry(
              fact.content,
              fact.category,
              sessionId,
            );
            this.addEntry(entry);
            result.entriesAdded++;
          }
        }
      } catch (err) {
        log.memory.warn(`[MemoryReflexion] LLM extraction failed: ${err}`);
        const facts = this.extractFactsHeuristic(transcript);
        for (const fact of facts) {
          if (!this.findSimilar(fact)) {
            const entry = this.createEntry(
              fact,
              categorizeFact(fact),
              sessionId,
            );
            this.addEntry(entry);
            result.entriesAdded++;
          }
        }
      }
    } else {
      const facts = this.extractFactsHeuristic(transcript);
      for (const fact of facts) {
        if (!this.findSimilar(fact)) {
          const entry = this.createEntry(fact, categorizeFact(fact), sessionId);
          this.addEntry(entry);
          result.entriesAdded++;
        }
      }
    }

    this.store.lastConsolidated = new Date().toISOString();
    await this.save();
    if (this.store.totalEntries > COMPRESSION_THRESHOLD)
      await this.maybeCompress();
    return result;
  }

  private async extractFactsWithLLM(
    transcript: string,
  ): Promise<{ content: string; category: MemoryCategory }[]> {
    const prompt = `Analyze this conversation and extract 3-8 IMPORTANT facts worth remembering.\n\nFocus on: decisions, preferences, project details, important context.\n\nCONVERSATION:\n${transcript}\n\nReturn ONLY a JSON array: [{"content": "fact", "category": "preference|fact|decision|project|context"}, ...]`;

    const response = await this.provider!.chat([
      {
        role: "system",
        content:
          "You are a memory extraction assistant. Output only valid JSON.",
      },
      { role: "user", content: prompt },
    ]);

    let jsonStr = response.content
      .trim()
      .replace(/^```json?\s*/i, "")
      .replace(/\s*```$/i, "")
      .trim();
    const parsed = JSON.parse(jsonStr);
    if (!Array.isArray(parsed)) return [];

    return parsed.slice(0, 8).map((item: any) => ({
      content: String(item.content ?? "").slice(0, 500),
      category: [
        "preference",
        "fact",
        "decision",
        "project",
        "context",
      ].includes(item.category)
        ? (item.category as MemoryCategory)
        : ("context" as MemoryCategory),
    }));
  }

  private extractFactsHeuristic(transcript: string): string[] {
    const facts: string[] = [];
    const patterns = [
      /i (prefer|like|hate|love|dislike) ([^.]+)/gi,
      /i('m| am) (a |an )?(new to|familiar with|working with|using) ([^.]+)/gi,
      /i want to ([^.]+)/gi,
      /i('ll| will) use ([^.]+)/gi,
      /decided to ([^.]+)/gi,
      /choosing ([^.]+)/gi,
    ];

    for (const pattern of patterns) {
      const matches = transcript.matchAll(pattern);
      for (const match of matches) {
        const fact = match[0].trim();
        if (fact.length > 10 && fact.length < 200) facts.push(fact);
      }
    }
    return [...new Set(facts)].slice(0, 5);
  }

  private createEntry(
    content: string,
    category: MemoryCategory,
    sessionId?: string,
  ): MemoryEntry {
    return {
      id: generateId(),
      content,
      category,
      source: "extraction",
      createdAt: new Date().toISOString(),
      lastAccessedAt: new Date().toISOString(),
      accessCount: 1,
      importance: 0.5,
      confidence: 0.8,
      relatedTopics: extractTopics(content),
      sessionId,
      owlName: this.owl?.persona.name,
    };
  }

  private addEntry(entry: MemoryEntry): void {
    this.store.entries[entry.id] = entry;
    if (!this.store.byCategory[entry.category])
      this.store.byCategory[entry.category] = [];
    this.store.byCategory[entry.category].push(entry.id);
    for (const topic of entry.relatedTopics) {
      if (!this.store.byTopic[topic]) this.store.byTopic[topic] = [];
      this.store.byTopic[topic].push(entry.id);
    }
  }

  private findSimilar(content: string): MemoryEntry | undefined {
    const normalized = content.toLowerCase().replace(/\s+/g, " ");
    for (const entry of Object.values(this.store.entries)) {
      const entryNormalized = entry.content.toLowerCase().replace(/\s+/g, " ");
      const entryWords = new Set(
        entryNormalized.split(" ").filter((w) => w.length > 3),
      );
      const searchWords = new Set(
        normalized.split(" ").filter((w) => w.length > 3),
      );
      const intersection = [...entryWords].filter((w) => searchWords.has(w));
      const similarity =
        intersection.length / Math.max(entryWords.size, searchWords.size);
      if (similarity > 0.7) return entry;
    }
    return undefined;
  }

  async reflex(): Promise<ReflexionResult> {
    const startTime = Date.now();
    await this.init();

    const result: ReflexionResult = {
      newEntries: [],
      compressedEntries: [],
      revivedEntries: [],
      insights: [],
      memoryHealth: 0,
      durationMs: 0,
    };

    result.compressedEntries = await this.compressOldEntries();
    if (this.provider) result.insights = await this.generateInsights();
    result.revivedEntries = await this.reviveForgotten();
    result.memoryHealth = this.calculateHealth();

    this.store.lastReflexed = new Date().toISOString();
    await this.save();
    result.durationMs = Date.now() - startTime;

    return result;
  }

  private async compressOldEntries(): Promise<string[]> {
    const compressed: string[] = [];
    const entries = Object.values(this.store.entries)
      .filter((e) => !e.compressed)
      .sort(
        (a, b) =>
          new Date(a.lastAccessedAt).getTime() -
          new Date(b.lastAccessedAt).getTime(),
      );

    for (const entry of entries
      .filter((e) => e.accessCount < 3 && e.importance < 0.5)
      .slice(0, ARCHIVE_BATCH_SIZE)) {
      entry.originalContent = entry.content;
      entry.content = entry.content.slice(0, 100) + "...";
      entry.compressed = true;
      compressed.push(entry.id);
    }
    return compressed;
  }

  private async generateInsights(): Promise<string[]> {
    if (!this.provider) return [];
    const recentEntries = Object.values(this.store.entries)
      .slice(0, 30)
      .map((e) => `[${e.category}]: ${e.content}`)
      .join("\n");

    try {
      const response = await this.provider.chat([
        {
          role: "system",
          content: "You are a memory analyst. Output only valid JSON.",
        },
        {
          role: "user",
          content: `Analyze these memories and identify patterns:\n\n${recentEntries}\n\nReturn 2-3 short insights as a JSON array.`,
        },
      ]);
      let jsonStr = response.content
        .trim()
        .replace(/^```json?\s*/i, "")
        .replace(/\s*```$/i, "")
        .trim();
      return JSON.parse(jsonStr) || [];
    } catch (err) {
      log.engine.warn(
        `[Reflexion] Insight generation failed: ${err instanceof Error ? err.message : err}`,
      );
      return [];
    }
  }

  private async reviveForgotten(): Promise<string[]> {
    const revived: string[] = [];
    const now = Date.now();
    for (const entry of Object.values(this.store.entries)) {
      const daysSinceAccess =
        (now - new Date(entry.lastAccessedAt).getTime()) /
        (1000 * 60 * 60 * 24);
      if (
        daysSinceAccess > REVIVAL_CHECK_INTERVAL_DAYS &&
        entry.importance > 0.3
      ) {
        entry.accessCount++;
        entry.lastAccessedAt = new Date().toISOString();
        revived.push(entry.id);
      }
    }
    return revived;
  }

  private calculateHealth(): number {
    const entries = Object.values(this.store.entries);
    if (entries.length === 0) return 100;
    let health = 100;
    if (entries.length > MAX_ENTRIES)
      health -= Math.min(30, (entries.length - MAX_ENTRIES) * 0.5);
    const daysSinceReflex =
      (Date.now() - new Date(this.store.lastReflexed || 0).getTime()) /
      (1000 * 60 * 60 * 24);
    if (daysSinceReflex > 14) health -= 20;
    else if (daysSinceReflex > 7) health -= 10;
    const avgImportance =
      entries.reduce((sum, e) => sum + e.importance, 0) / entries.length;
    if (avgImportance < 0.3) health -= 15;
    return Math.max(0, Math.min(100, Math.round(health)));
  }

  async retrieve(
    query: string,
    maxEntries: number = 10,
  ): Promise<MemoryEntry[]> {
    await this.init();
    const queryWords = query
      .toLowerCase()
      .split(/\s+/)
      .filter((w) => w.length > 2);

    const scored = Object.values(this.store.entries).map((entry) => {
      let score = entry.importance * 10;
      const contentLower = entry.content.toLowerCase();
      for (const word of queryWords) {
        if (contentLower.includes(word)) score += 5;
      }
      if (entry.category === "preference") score += 3;
      if (entry.category === "decision") score += 2;
      return { entry, score };
    });

    const results = scored
      .sort((a, b) => b.score - a.score)
      .slice(0, maxEntries)
      .map((s) => {
        s.entry.accessCount++;
        s.entry.lastAccessedAt = new Date().toISOString();
        return s.entry;
      });

    this.save().catch((err) => {
      log.engine.warn(
        `[Reflexion] Save failed after retrieval: ${err instanceof Error ? err.message : err}`,
      );
    });
    return results;
  }

  async getForSystemPrompt(maxChars: number = 4000): Promise<string> {
    await this.init();
    const entries = Object.values(this.store.entries)
      .filter((e) => !e.compressed)
      .sort((a, b) => b.importance - a.importance);
    const lines: string[] = ["## Known Facts & Preferences"];
    const categories: MemoryCategory[] = [
      "preference",
      "decision",
      "project",
      "fact",
      "context",
    ];

    for (const cat of categories) {
      const catEntries = entries.filter((e) => e.category === cat);
      if (catEntries.length === 0) continue;
      lines.push(`\n### ${cat.charAt(0).toUpperCase() + cat.slice(1)}s`);
      for (const entry of catEntries.slice(0, 10)) {
        const importance =
          entry.importance > 0.7 ? "🔴" : entry.importance > 0.4 ? "🟡" : "⚪";
        lines.push(`${importance} ${entry.content}`);
      }
    }

    const full = lines.join("\n");
    return full.length > maxChars
      ? full.slice(0, maxChars) + "\n\n[...more memories truncated]"
      : full;
  }

  getStats(): {
    total: number;
    byCategory: Record<string, number>;
    health: number;
    lastReflex: string;
  } {
    const byCategory: Record<string, number> = {};
    for (const [cat, ids] of Object.entries(this.store.byCategory))
      byCategory[cat] = ids.length;
    return {
      total: this.store.totalEntries,
      byCategory,
      health: this.calculateHealth(),
      lastReflex: this.store.lastReflexed || "never",
    };
  }

  async delete(id: string): Promise<void> {
    const entry = this.store.entries[id];
    if (!entry) return;
    delete this.store.entries[id];
    this.store.byCategory[entry.category] = this.store.byCategory[
      entry.category
    ].filter((i) => i !== id);
    await this.save();
  }

  private async maybeCompress(): Promise<void> {
    if (Object.keys(this.store.entries).length <= MAX_ENTRIES) return;
    await this.compressOldEntries();
    await this.save();
  }
}
