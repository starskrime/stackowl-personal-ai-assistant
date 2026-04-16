/**
 * StackOwl — Knowledge Synthesizer
 * Multi-pipeline synthesis engine.
 */

import { v4 as uuidv4 } from "uuid";
import { log } from "../logger.js";
import { webFetch } from "../browser/smart-fetch.js";
import type { ModelProvider } from "../providers/base.js";
import type { OwlInstance } from "../owls/persona.js";
import type { StackOwlConfig } from "../config/loader.js";
import type { PelletStore, Pellet } from "../pellets/store.js";
import { KnowledgeGraphManager } from "./knowledge-graph.js";
import type { SynthesisStrategy, FusedTopic } from "./topic-fusion.js";

export interface SynthesisContext {
  topic: FusedTopic;
  recentMessages?: string[];
  existingPellets?: Pellet[];
  userExplicitRequest?: boolean;
}

export interface SynthesisResult {
  pipeline: SynthesisStrategy;
  topic: string;
  pellets: Pellet[];
  relatedTopics: string[];
  sources: string[];
  confidence: number;
  learnedAt: string;
  durationMs: number;
  success: boolean;
  error?: string;
}

export interface SynthesisReport {
  totalTopics: number;
  successful: number;
  failed: number;
  pelletsCreated: number;
  byPipeline: Record<string, number>;
  durationMs: number;
}

const MAX_PELLETS = 2000;
const EVICT_COUNT = 10;
const Q_AND_A_QUESTIONS = 1;
const WEB_RESEARCH_MAX_URLS = 2;
/**
 * Hard cap on LLM calls per synthesize() invocation.
 * Prevents runaway token burn from nested loops.
 * With cap of 4: 1 question-gen + 1 answer + 2 web extractions = 4 calls max.
 */
const MAX_LLM_CALLS_PER_CYCLE = 4;

/** Per-call LLM budget — isolated per synthesize() invocation to prevent race conditions. */
interface LlmBudget {
  count: number;
}

export class KnowledgeSynthesizer {
  private graphManager: KnowledgeGraphManager;

  constructor(
    private provider: ModelProvider,
    private owl: OwlInstance,
    _config: StackOwlConfig,
    private pelletStore: PelletStore,
    workspacePath: string,
  ) {
    this.graphManager = new KnowledgeGraphManager(workspacePath);
  }

  /** Budget-guarded LLM call. Throws if budget exhausted. */
  private async budgetedChat(
    budget: LlmBudget,
    messages: Parameters<ModelProvider["chat"]>[0],
    model?: string,
    options?: Parameters<ModelProvider["chat"]>[2],
  ): ReturnType<ModelProvider["chat"]> {
    if (budget.count >= MAX_LLM_CALLS_PER_CYCLE) {
      throw new Error(
        `LLM budget exhausted (${MAX_LLM_CALLS_PER_CYCLE} calls). Stopping synthesis to prevent token burn.`,
      );
    }
    budget.count++;
    log.evolution.info(
      `[Synthesizer] LLM call ${budget.count}/${MAX_LLM_CALLS_PER_CYCLE}`,
    );
    return this.provider.chat(messages, model, options);
  }

  async synthesize(
    topics: FusedTopic[],
    context?: SynthesisContext["recentMessages"],
  ): Promise<SynthesisReport> {
    const startTime = Date.now();
    const budget: LlmBudget = { count: 0 }; // Per-call budget — isolated from concurrent invocations
    await this.graphManager.load();

    const report: SynthesisReport = {
      totalTopics: topics.length,
      successful: 0,
      failed: 0,
      pelletsCreated: 0,
      byPipeline: {},
      durationMs: 0,
    };

    for (const topic of topics) {
      if (
        topic.synthesisStrategy === "quick_lookup" &&
        !topic.priorityOverride &&
        topic.urgency < 20
      ) {
        continue;
      }

      try {
        const result = await this.synthesizeSingle({
          topic,
          recentMessages: context,
        }, budget);
        if (result.success) {
          report.successful++;
          report.pelletsCreated += result.pellets.length;
        } else {
          report.failed++;
        }
        report.byPipeline[result.pipeline] =
          (report.byPipeline[result.pipeline] || 0) + 1;
        this.graphManager.recordStudy(
          topic.normalizedName,
          result.pellets.length,
          result.relatedTopics,
        );
      } catch (err) {
        report.failed++;
        log.evolution.warn(
          `[Synthesizer] Failed for "${topic.displayName}" (strategy: ${topic.synthesisStrategy}): ` +
            `${err instanceof Error ? `${err.message}\n${err.stack}` : err}`,
        );
      }
    }

    await this.graphManager.save();
    report.durationMs = Date.now() - startTime;

    // Always log the outcome — success or failure
    if (report.failed > 0 || report.pelletsCreated === 0) {
      log.evolution.warn(
        `[Synthesizer] Completed: ${report.successful}/${report.totalTopics} succeeded, ` +
          `${report.failed} failed, ${report.pelletsCreated} pellets in ${report.durationMs}ms` +
          (report.pelletsCreated === 0 ? " — NO PELLETS CREATED" : ""),
      );
    } else {
      log.evolution.info(
        `[Synthesizer] Completed: ${report.pelletsCreated} pellets from ${report.successful} topics in ${report.durationMs}ms`,
      );
    }

    return report;
  }

  async synthesizeSingle(ctx: SynthesisContext, budget: LlmBudget = { count: 0 }): Promise<SynthesisResult> {
    const startTime = Date.now();
    const { topic } = ctx;
    let result: SynthesisResult;

    switch (topic.synthesisStrategy) {
      case "deep_research":
        result = await this.runDeepResearch(topic, budget);
        break;
      case "web_research":
        result = await this.runWebResearch(topic, budget);
        break;
      case "document_digest":
        result = await this.runDocumentDigest(topic);
        break;
      case "repo_analysis":
        result = await this.runRepoAnalysis(topic);
        break;
      case "q_and_a":
        result = await this.runQAndA(topic, budget);
        break;
      case "quick_lookup":
        result = await this.runQuickLookup(topic, budget);
        break;
      default:
        result = await this.runQAndA(topic, budget);
    }

    result.durationMs = Date.now() - startTime;
    return result;
  }

  private async runQAndA(topic: FusedTopic, budget: LlmBudget): Promise<SynthesisResult> {
    const pellets: Pellet[] = [];
    const allRelatedTopics: string[] = [];
    await this.ensureCapacity(EVICT_COUNT);
    const questions = await this.generateQuestions(topic, budget);

    for (const question of questions.slice(0, Q_AND_A_QUESTIONS)) {
      try {
        const { pellet, relatedTopics } = await this.answerAndStore(
          topic,
          question,
          budget,
        );
        pellets.push(pellet);
        allRelatedTopics.push(...relatedTopics);
      } catch (err) {
        log.evolution.warn(`  Q&A failed for: ${question.slice(0, 50)}`);
      }
    }

    return {
      pipeline: "q_and_a",
      topic: topic.displayName,
      pellets,
      relatedTopics: [...new Set(allRelatedTopics)].slice(0, 6),
      sources: [],
      confidence: pellets.length > 0 ? 0.7 + pellets.length * 0.05 : 0,
      learnedAt: new Date().toISOString(),
      durationMs: 0,
      success: pellets.length > 0,
    };
  }

  private async generateQuestions(topic: FusedTopic, budget: LlmBudget): Promise<string[]> {
    const context = topic.originalSignals.join("; ");
    const prompt = `Generate ${Q_AND_A_QUESTIONS} targeted questions about "${topic.displayName}".\n\nContext: ${context || "No prior context."}\n\nRules: Focus on HOW to do it. Be specific. Return ONLY a JSON array of strings.`;

    try {
      const response = await this.budgetedChat(budget, [
        {
          role: "system",
          content:
            "You are a research question generator. Output only valid JSON.",
        },
        { role: "user", content: prompt },
      ]);
      const questions = this.parseJsonArray(response.content);
      if (questions.length > 0) return questions;
    } catch (err) {
      log.evolution.warn(
        `[Synthesizer] Question generation failed for "${topic.displayName}": ${err instanceof Error ? err.message : err}`,
      );
    }

    return [
      `How can an AI assistant help with ${topic.displayName}?`,
      `What are common requests related to ${topic.displayName}?`,
      `What commands/APIs should an AI assistant know for ${topic.displayName}?`,
    ];
  }

  private async answerAndStore(
    topic: FusedTopic,
    question: string,
    budget: LlmBudget,
  ): Promise<{ pellet: Pellet; relatedTopics: string[] }> {
    const prompt = `Question: "${question}"\n\nWrite a concise knowledge card (max 150 words).\n\n**Answer:**\n**How to do it:**\n**Example:**\n\nEnd with: RELATED_JSON: ["topic1", "topic2", "topic3"]`;
    const response = await this.budgetedChat(
      budget,
      [
        {
          role: "system",
          content: `You are ${this.owl.persona.name}, generating knowledge cards.`,
        },
        { role: "user", content: prompt },
      ],
      undefined,
      { temperature: 0.2 },
    );

    const relatedTopics = this.extractRelatedJson(response.content);
    const cleanContent = response.content
      .replace(/RELATED_JSON:\s*\[[\s\S]*?\]\s*/g, "")
      .trim();
    const pellet = this.createPellet(topic, question, cleanContent);
    await this.pelletStore.save(pellet);
    return { pellet, relatedTopics };
  }

  private async runWebResearch(topic: FusedTopic, budget: LlmBudget): Promise<SynthesisResult> {
    const pellets: Pellet[] = [];
    const sources: string[] = [];
    await this.ensureCapacity(EVICT_COUNT * 2);
    const urls = await this.findRelevantUrls(topic);

    if (urls.length === 0) return this.runQAndA(topic, budget);

    for (const url of urls.slice(0, WEB_RESEARCH_MAX_URLS)) {
      try {
        const content = await this.crawlUrl(url);
        if (content) {
          const extractedPellets = await this.synthesizeFromContent(
            topic,
            content,
            url,
            budget,
          );
          pellets.push(...extractedPellets);
          sources.push(url);
        }
      } catch (err) {
        log.evolution.warn(`[Synthesizer] Failed to crawl ${url}: ${err}`);
      }
    }

    return {
      pipeline: "web_research",
      topic: topic.displayName,
      pellets,
      relatedTopics: [],
      sources,
      confidence: pellets.length > 0 ? 0.85 : 0,
      learnedAt: new Date().toISOString(),
      durationMs: 0,
      success: pellets.length > 0,
    };
  }

  /**
   * Find relevant URLs via real DuckDuckGo search instead of asking the LLM
   * to hallucinate URLs (which always produces non-existent pages).
   */
  private async findRelevantUrls(topic: FusedTopic): Promise<string[]> {
    const query = topic.displayName + (topic.sourceSignals.includes("gap")
      ? " tutorial guide"
      : " best practices");

    try {
      const searchUrl = `https://html.duckduckgo.com/html/?q=${encodeURIComponent(query)}`;
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 15000);

      const response = await fetch(searchUrl, {
        signal: controller.signal,
        headers: {
          "User-Agent":
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
          Accept: "text/html",
        },
      });
      clearTimeout(timeoutId);

      if (!response.ok) {
        log.evolution.warn(
          `[Synthesizer] DuckDuckGo search HTTP ${response.status} for "${topic.displayName}"`,
        );
        return [];
      }

      const html = await response.text();
      const urls: string[] = [];
      // Extract result URLs from DuckDuckGo HTML response
      const urlRegex = /class="result__a"[^>]+href="([^"]+)"/gi;
      let match: RegExpExecArray | null;
      while ((match = urlRegex.exec(html)) !== null && urls.length < WEB_RESEARCH_MAX_URLS) {
        let url = match[1];
        // DuckDuckGo wraps URLs in a redirect — extract the real destination
        const uddg = url.match(/uddg=([^&]+)/);
        if (uddg) url = decodeURIComponent(uddg[1]);
        if (url.startsWith("http")) urls.push(url);
      }

      log.evolution.info(
        `[Synthesizer] DuckDuckGo found ${urls.length} URLs for "${topic.displayName}"`,
      );
      return urls;
    } catch (err) {
      log.evolution.warn(
        `[Synthesizer] URL discovery failed for "${topic.displayName}": ${err instanceof Error ? err.message : err}`,
      );
      return [];
    }
  }

  private async crawlUrl(url: string): Promise<string | null> {
    try {
      const result = await webFetch(url, { maxLength: 5000, timeout: 15000 });
      if (result.blocked) {
        log.evolution.warn(
          `[Synthesizer] Blocked (${result.blockType}) fetching ${url}`,
        );
        return null;
      }
      return result.text || null;
    } catch (err) {
      log.evolution.warn(
        `[Synthesizer] Crawl failed for ${url}: ${err instanceof Error ? err.message : err}`,
      );
      return null;
    }
  }

  private async synthesizeFromContent(
    topic: FusedTopic,
    content: string,
    source: string,
    budget: LlmBudget,
  ): Promise<Pellet[]> {
    const pellets: Pellet[] = [];
    const prompt = `Based on this content from ${source}, extract 2-3 key points about "${topic.displayName}". Return a JSON array of objects with "key_point" and "how_to".`;
    try {
      const response = await this.budgetedChat(budget, [
        {
          role: "system",
          content: "You are a knowledge extraction assistant.",
        },
        {
          role: "user",
          content: `${prompt}\n\nContent:\n${content.slice(0, 3000)}`,
        },
      ]);
      // parseJsonArray returns the parsed array directly — elements may be
      // objects (from LLM returning [{key_point, how_to}]) or strings.
      const extracted = this.parseJsonArray(response.content) as Array<
        string | { key_point?: string; how_to?: string }
      >;
      for (const raw of extracted) {
        const item = typeof raw === "string" ? { key_point: raw, how_to: "" } : raw;
        const pellet = this.createPellet(
          topic,
          item.key_point || topic.displayName,
          `${item.key_point || ""}\n\n${item.how_to || ""}`,
        );
        pellets.push(pellet);
        await this.pelletStore.save(pellet);
      }
    } catch (err) {
      log.evolution.warn(
        `[Synthesizer] Content extraction failed for ${source}: ${err instanceof Error ? err.message : err}`,
      );
      const pellet = this.createPellet(
        topic,
        `Learnings from ${source}`,
        content.slice(0, 500),
      );
      pellets.push(pellet);
      await this.pelletStore.save(pellet);
    }
    return pellets;
  }

  private async runDocumentDigest(topic: FusedTopic): Promise<SynthesisResult> {
    const pellets: Pellet[] = [];
    const sources: string[] = [];
    const docPaths = ["./docs/README.md", "./README.md"];

    for (const path of docPaths) {
      try {
        const { readFile } = await import("node:fs/promises");
        const content = await readFile(path, "utf-8").catch(() => null);
        if (content) {
          const pellet = this.createPellet(
            topic,
            `Document: ${path}`,
            content.slice(0, 500),
          );
          pellets.push(pellet);
          await this.pelletStore.save(pellet);
          sources.push(path);
        }
      } catch (err) {
        log.evolution.warn(
          `[Synthesizer] Document read failed for ${path}: ${err instanceof Error ? err.message : err}`,
        );
      }
    }

    // Previously cascaded to runWebResearch() here, burning 5+ LLM calls.
    // Now we just return empty — the topic stays in the knowledge graph for future study.

    return {
      pipeline: "document_digest",
      topic: topic.displayName,
      pellets,
      relatedTopics: [],
      sources,
      confidence: 0.9,
      learnedAt: new Date().toISOString(),
      durationMs: 0,
      success: pellets.length > 0,
    };
  }

  private async runRepoAnalysis(topic: FusedTopic): Promise<SynthesisResult> {
    const pellets: Pellet[] = [];
    const sources: string[] = [];

    try {
      const { readFile } = await import("node:fs/promises");
      const pkg = JSON.parse(
        await readFile("./package.json", "utf-8").catch(() => "{}"),
      );
      const deps = Object.keys(pkg.dependencies || {});
      if (deps.length > 0) {
        const pellet = this.createPellet(
          topic,
          "Project dependencies",
          `Dependencies: ${deps.join(", ")}`,
        );
        pellets.push(pellet);
        await this.pelletStore.save(pellet);
        sources.push("./package.json");
      }
    } catch (err) {
      log.evolution.warn(
        `[Synthesizer] Repo analysis failed: ${err instanceof Error ? err.message : err}`,
      );
    }

    return {
      pipeline: "repo_analysis",
      topic: topic.displayName,
      pellets,
      relatedTopics: [],
      sources,
      confidence: pellets.length > 0 ? 0.85 : 0,
      learnedAt: new Date().toISOString(),
      durationMs: 0,
      success: pellets.length > 0,
    };
  }

  private async runQuickLookup(topic: FusedTopic, budget: LlmBudget): Promise<SynthesisResult> {
    const prompt = `Give ONE sentence answer about "${topic.displayName}". Be practical. Return ONLY the sentence.`;
    try {
      const response = await this.budgetedChat(budget, [
        { role: "system", content: "Be concise. One sentence only." },
        { role: "user", content: prompt },
      ]);
      const pellet = this.createPellet(
        topic,
        topic.displayName,
        response.content.trim(),
      );
      await this.pelletStore.save(pellet);
      return {
        pipeline: "quick_lookup",
        topic: topic.displayName,
        pellets: [pellet],
        relatedTopics: [],
        sources: [],
        confidence: 0.5,
        learnedAt: new Date().toISOString(),
        durationMs: 0,
        success: true,
      };
    } catch (err) {
      return {
        pipeline: "quick_lookup",
        topic: topic.displayName,
        pellets: [],
        relatedTopics: [],
        sources: [],
        confidence: 0,
        learnedAt: new Date().toISOString(),
        durationMs: 0,
        success: false,
        error: String(err),
      };
    }
  }

  /**
   * Deep research — DOWNGRADED to single Q&A pipeline.
   * Previously ran Q&A + web research in parallel (9+ LLM calls per topic).
   * Now just delegates to Q&A (2 LLM calls: 1 question-gen + 1 answer).
   * This prevents the 300-500 call token burn from nested loops.
   */
  private async runDeepResearch(topic: FusedTopic, budget: LlmBudget): Promise<SynthesisResult> {
    log.evolution.info(`[Synthesizer] Deep research (capped): ${topic.displayName}`);
    const result = await this.runQAndA(topic, budget);
    result.pipeline = "deep_research";
    return result;
  }

  private createPellet(
    topic: FusedTopic,
    title: string,
    content: string,
  ): Pellet {
    const slug = `learn-${topic.normalizedName.slice(0, 20)}-${uuidv4().substring(0, 6)}`;
    return {
      id: slug,
      title: title.slice(0, 200),
      generatedAt: new Date().toISOString(),
      source: `synthesizer:${topic.normalizedName}:${topic.synthesisStrategy}`,
      owls: [this.owl.persona.name],
      tags: [
        topic.normalizedName,
        topic.synthesisStrategy,
        ...topic.sourceSignals,
      ],
      version: 1,
      content: content.slice(0, 2000),
    };
  }

  private async ensureCapacity(needSpace: number): Promise<void> {
    const existing = await this.pelletStore.listAll();
    if (existing.length + needSpace > MAX_PELLETS) {
      for (const pellet of existing.slice(-needSpace)) {
        await this.pelletStore.delete(pellet.id);
      }
    }
  }

  private parseJsonArray(content: string): string[] {
    try {
      const cleaned = content
        .trim()
        .replace(/^```json?\s*/i, "")
        .replace(/\s*```$/i, "")
        .trim();
      if (cleaned.startsWith("[")) return JSON.parse(cleaned);
      const match = cleaned.match(/\[[\s\S]*\]/);
      return match ? JSON.parse(match[0]) : [];
    } catch (err) {
      log.evolution.warn(
        `[Synthesizer] JSON parse failed: ${err instanceof Error ? err.message : err}`,
      );
      return [];
    }
  }

  private extractRelatedJson(content: string): string[] {
    const match = content.match(/RELATED_JSON:\s*(\[[\s\S]*?\])/);
    if (!match) return [];
    try {
      const p = JSON.parse(match[1]);
      return Array.isArray(p)
        ? p.filter((t: unknown) => typeof t === "string")
        : [];
    } catch (err) {
      log.evolution.warn(
        `[Synthesizer] Related JSON parse failed: ${err instanceof Error ? err.message : err}`,
      );
      return [];
    }
  }
}
