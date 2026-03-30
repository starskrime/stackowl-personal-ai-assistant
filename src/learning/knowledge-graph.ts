/**
 * StackOwl — Knowledge Graph
 *
 * Tracks the owl's growing understanding of different domains.
 * Each domain has a depth score (0-1) that grows with study sessions,
 * a frontier of related topics to explore, and a study queue.
 *
 * This is the "memory of learning" — it makes knowledge compound over time.
 * Persisted to workspace/knowledge_graph.json.
 */

import { readFile, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join } from "node:path";

export interface DomainNode {
  /** How deeply the owl knows this domain (0 = just heard of it, 1 = expert) */
  depth: number;
  /** ISO timestamp of last study session for this domain */
  lastStudied: string;
  /** Number of knowledge pellets stored about this domain */
  pelletCount: number;
  /** Total number of dedicated study sessions */
  studyCount: number;
  /** Related topics discovered during research — the learning frontier */
  relatedTopics: string[];
  /** How this domain was first encountered */
  source: "conversation" | "self-study" | "frontier";
}

export interface KnowledgeGraph {
  domains: Record<string, DomainNode>;
  /** Ordered list of topics to study next (FIFO, prioritized by gap and recency) */
  studyQueue: string[];
  lastUpdated: string;
}

const GRAPH_FILE = "knowledge_graph.json";

// Depth gain per study session — starts fast, slows as expertise grows
const DEPTH_GAIN = 0.15;
// How long before a domain is eligible for re-study (in ms)
const RESTUDY_COOLDOWN_MS = 24 * 60 * 60 * 1000; // 24 hours

export class KnowledgeGraphManager {
  private graphPath: string;
  private graph: KnowledgeGraph = {
    domains: {},
    studyQueue: [],
    lastUpdated: new Date().toISOString(),
  };

  constructor(workspacePath: string) {
    this.graphPath = join(workspacePath, GRAPH_FILE);
  }

  async load(): Promise<void> {
    if (!existsSync(this.graphPath)) return;
    try {
      const raw = await readFile(this.graphPath, "utf-8");
      this.graph = JSON.parse(raw);
    } catch {
      // Corrupt file — start fresh
    }
  }

  /**
   * Return a snapshot of the current graph for use by TopicFusionEngine.
   * Avoids the need for type-unsafe bracket notation access to private fields.
   */
  getGraph(): KnowledgeGraph {
    return this.graph;
  }

  async save(): Promise<void> {
    this.graph.lastUpdated = new Date().toISOString();
    await writeFile(
      this.graphPath,
      JSON.stringify(this.graph, null, 2),
      "utf-8",
    );
  }

  /**
   * Register that a domain appeared in a conversation.
   * Creates the node at low depth, queues it for study if new.
   */
  touchDomain(
    domain: string,
    source: DomainNode["source"] = "conversation",
  ): void {
    const normalized = domain.trim().toLowerCase();
    if (!normalized) return;

    if (!this.graph.domains[normalized]) {
      this.graph.domains[normalized] = {
        depth: 0.05,
        lastStudied: "",
        pelletCount: 0,
        studyCount: 0,
        relatedTopics: [],
        source,
      };
      // Queue for study if not already there
      if (!this.graph.studyQueue.includes(normalized)) {
        this.graph.studyQueue.push(normalized);
      }
    }
  }

  /**
   * Record that a domain was deeply studied.
   * Increases depth, logs pellets created, and expands the learning frontier
   * by adding related topics as new study candidates.
   */
  recordStudy(
    domain: string,
    pelletsCreated: number,
    relatedTopics: string[],
  ): void {
    const normalized = domain.trim().toLowerCase();

    const existing = this.graph.domains[normalized] ?? {
      depth: 0,
      lastStudied: "",
      pelletCount: 0,
      studyCount: 0,
      relatedTopics: [],
      source: "self-study" as const,
    };

    // Depth gain diminishes as we get closer to 1 (like real expertise)
    const gainMultiplier = 1 - existing.depth * 0.5;
    existing.depth = Math.min(
      1.0,
      existing.depth + DEPTH_GAIN * gainMultiplier,
    );
    existing.lastStudied = new Date().toISOString();
    existing.pelletCount += pelletsCreated;
    existing.studyCount += 1;

    // Merge in newly discovered related topics
    const newRelated = relatedTopics
      .map((t) => t.trim().toLowerCase())
      .filter(Boolean);
    existing.relatedTopics = [
      ...new Set([...existing.relatedTopics, ...newRelated]),
    ];

    this.graph.domains[normalized] = existing;

    // Remove from study queue — we just studied it
    this.graph.studyQueue = this.graph.studyQueue.filter(
      (t) => t !== normalized,
    );

    // Add frontier topics to study queue (only if we haven't studied them before)
    for (const related of newRelated) {
      if (
        !this.graph.domains[related] &&
        !this.graph.studyQueue.includes(related)
      ) {
        this.graph.studyQueue.push(related);
        // Register as a node at near-zero depth
        this.graph.domains[related] = {
          depth: 0.02,
          lastStudied: "",
          pelletCount: 0,
          studyCount: 0,
          relatedTopics: [],
          source: "frontier",
        };
      }
    }
  }

  /**
   * Get the top N topics to study next.
   * Priority order:
   *   1. Explicit study queue (topics from conversations + frontiers)
   *   2. Domains with low depth not recently studied
   */
  getStudyQueue(maxTopics = 3): string[] {
    const now = Date.now();

    // Filter queue to topics eligible for study (past cooldown)
    const readyFromQueue = this.graph.studyQueue.filter((topic) => {
      const node = this.graph.domains[topic];
      if (!node) return true; // Unknown domain — always study
      if (!node.lastStudied) return true; // Never studied
      const elapsed = now - new Date(node.lastStudied).getTime();
      return elapsed > RESTUDY_COOLDOWN_MS;
    });

    if (readyFromQueue.length >= maxTopics) {
      return readyFromQueue.slice(0, maxTopics);
    }

    // Fill remaining slots with low-depth domains not in queue
    const lowDepth = Object.entries(this.graph.domains)
      .filter(([name, node]) => {
        if (readyFromQueue.includes(name)) return false;
        if (node.depth >= 0.85) return false; // Already quite deep
        if (!node.lastStudied) return true;
        const elapsed = now - new Date(node.lastStudied).getTime();
        return elapsed > RESTUDY_COOLDOWN_MS;
      })
      .sort(([, a], [, b]) => a.depth - b.depth) // Lowest depth first
      .map(([name]) => name);

    return [...readyFromQueue, ...lowDepth].slice(0, maxTopics);
  }

  getStats(): {
    totalDomains: number;
    avgDepth: number;
    studyQueueLength: number;
  } {
    const nodes = Object.values(this.graph.domains);
    const avgDepth =
      nodes.length > 0
        ? nodes.reduce((sum, d) => sum + d.depth, 0) / nodes.length
        : 0;
    return {
      totalDomains: nodes.length,
      avgDepth: Math.round(avgDepth * 100) / 100,
      studyQueueLength: this.graph.studyQueue.length,
    };
  }

  /**
   * Human-readable summary of what the owl knows best.
   */
  getDomainSummary(): string {
    const entries = Object.entries(this.graph.domains)
      .sort(([, a], [, b]) => b.depth - a.depth)
      .slice(0, 8);

    if (entries.length === 0) return "Nothing studied yet.";

    return entries
      .map(
        ([name, node]) =>
          `${name} ${Math.round(node.depth * 100)}%` +
          (node.pelletCount > 0 ? ` (${node.pelletCount} pellets)` : ""),
      )
      .join(", ");
  }

  /**
   * Get full domain report for display.
   */
  getFullReport(): string {
    const stats = this.getStats();
    const queue = this.graph.studyQueue.slice(0, 5);
    const domains = Object.entries(this.graph.domains).sort(
      ([, a], [, b]) => b.depth - a.depth,
    );

    const lines: string[] = [
      `## Knowledge Graph`,
      `Total domains: ${stats.totalDomains} | Avg depth: ${Math.round(stats.avgDepth * 100)}% | Queue: ${stats.studyQueueLength}`,
      "",
    ];

    if (domains.length > 0) {
      lines.push("### Domains (by depth)");
      for (const [name, node] of domains) {
        const bar =
          "█".repeat(Math.round(node.depth * 10)) +
          "░".repeat(10 - Math.round(node.depth * 10));
        lines.push(
          `${bar} ${Math.round(node.depth * 100)}%  ${name}  (${node.pelletCount} pellets, ${node.studyCount} sessions)`,
        );
      }
      lines.push("");
    }

    if (queue.length > 0) {
      lines.push(`### Study Queue (next up)`);
      for (const topic of queue) {
        lines.push(`  → ${topic}`);
      }
    }

    return lines.join("\n");
  }
}
