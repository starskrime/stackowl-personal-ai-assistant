/**
 * StackOwl — Conversation Digest (L1 Working Memory)
 *
 * A per-session semantic snapshot that survives message truncation.
 * Updated heuristically after every turn — zero LLM cost.
 *
 * Inspired by Zep's "Session Memory" concept: a mutable structured summary
 * of the active conversation, injected at the TOP of every prompt so the
 * model always knows what it just did, what it found, and what failed —
 * without re-reading raw tool result JSON in the message history.
 *
 * Fixes the "AI news bug": user asks for news → model formats nicely →
 * user asks for source links → model says "what links?".
 * The digest captures "URLs I found last turn" and surfaces them immediately.
 */

import { readFile, writeFile, mkdir } from "node:fs/promises";
import { existsSync } from "node:fs";
import { join } from "node:path";
import type { ChatMessage } from "../providers/base.js";
import { log } from "../logger.js";

// ─── Types ────────────────────────────────────────────────────────

export interface DigestArtifact {
  type: "url" | "file" | "data" | "command";
  value: string;
  label?: string; // e.g. "TechCrunch AI article", "downloaded video"
}

export interface ConversationDigest {
  sessionId: string;
  /** What the user is currently trying to accomplish */
  task: string;
  /** Artifacts produced in the last response: URLs, files, extracted data */
  artifacts: DigestArtifact[];
  /** Decisions made: "using yt-dlp", "MP4 format chosen" */
  decisions: string[];
  /** Approaches that didn't work */
  failed: string[];
  /** Things still unresolved */
  openQuestions: string[];
  updatedAt: string;
}

// ─── Constants ────────────────────────────────────────────────────

const URL_PATTERN = /https?:\/\/[^\s"'<>\])\}]+/g;
const FILE_WRITTEN_PATTERN = /(?:wrote|created|saved|written)\s+(?:file\s+)?["']?([^\s"'<>]+\.[a-z]{2,6})["']?/gi;
const COMMAND_PATTERN = /(?:running|executed|ran)\s*[`"]([^`"]{5,80})[`"]/gi;
const DECISION_PATTERN = /(?:I(?:'ll| will| am going to)?\s+use\s+|using\s+|chosen?\s+approach[:\s]+|decided\s+to\s+use\s+)([^\n.]{5,80})/gi;
const FAILURE_PATTERN = /(?:failed|error:|didn['']t work|try\s+again|attempt\s+\d+|retry|not\s+supported|permission\s+denied|404|403|500)[\s:]+([^\n.]{5,120})/gi;
const OPEN_Q_PATTERN = /(?:unclear|not\s+sure|need\s+to\s+(?:check|confirm|ask|verify)|(?:do\s+you\s+want|would\s+you\s+prefer|which|should\s+I))[^\n.?]{5,100}[?]/gi;

const MAX_ARTIFACTS = 12;
const MAX_DECISIONS = 6;
const MAX_FAILED = 6;
const MAX_OPEN_Q = 4;
const MAX_LABEL_LEN = 60;

// ─── Manager ──────────────────────────────────────────────────────

export class ConversationDigestManager {
  private cache: Map<string, ConversationDigest> = new Map();
  private digestsDir: string;

  constructor(workspacePath: string) {
    this.digestsDir = join(workspacePath, "memory", "digests");
  }

  // ── Load / Save ─────────────────────────────────────────────────

  async load(sessionId: string): Promise<ConversationDigest | null> {
    // Memory-first
    if (this.cache.has(sessionId)) return this.cache.get(sessionId)!;

    const filePath = this.filePath(sessionId);
    if (!existsSync(filePath)) return null;

    try {
      const raw = await readFile(filePath, "utf-8");
      const digest = JSON.parse(raw) as ConversationDigest;
      this.cache.set(sessionId, digest);
      return digest;
    } catch {
      return null;
    }
  }

  async save(digest: ConversationDigest): Promise<void> {
    this.cache.set(digest.sessionId, digest);
    try {
      if (!existsSync(this.digestsDir)) {
        await mkdir(this.digestsDir, { recursive: true });
      }
      await writeFile(
        this.filePath(digest.sessionId),
        JSON.stringify(digest, null, 2),
        "utf-8",
      );
    } catch (err) {
      log.engine.warn(
        `[ConversationDigest] Save failed for ${digest.sessionId}: ${err instanceof Error ? err.message : err}`,
      );
    }
  }

  async delete(sessionId: string): Promise<void> {
    this.cache.delete(sessionId);
  }

  // ── Update — called from PostProcessor after every response ─────

  /**
   * Extract semantic state from the latest exchange and save.
   * Pure heuristic — no LLM call, zero latency.
   */
  async update(
    sessionId: string,
    messages: ChatMessage[],
  ): Promise<ConversationDigest> {
    const existing = (await this.load(sessionId)) ?? this.blank(sessionId);

    // Find the latest user message (task)
    const lastUser = [...messages].reverse().find((m) => m.role === "user");
    const task = lastUser?.content?.slice(0, 200) ?? existing.task;

    // Collect all tool result + assistant messages for extraction
    const recent = messages.slice(-20); // last 20 messages only
    const textToMine = recent
      .filter((m) => m.role === "tool" || m.role === "assistant")
      .map((m) => m.content ?? "")
      .join("\n");

    const artifacts = this.extractArtifacts(textToMine, existing.artifacts);
    const decisions = this.extractDecisions(textToMine, existing.decisions);
    const failed = this.extractFailures(textToMine, existing.failed);
    const openQuestions = this.extractOpenQuestions(textToMine);

    const digest: ConversationDigest = {
      sessionId,
      task,
      artifacts,
      decisions,
      failed,
      openQuestions,
      updatedAt: new Date().toISOString(),
    };

    await this.save(digest);
    log.engine.debug(
      `[ConversationDigest] Updated: ${artifacts.length} artifacts, ${decisions.length} decisions, ${failed.length} failures`,
    );
    return digest;
  }

  // ── Format for injection into system prompt ──────────────────────

  toContextString(digest: ConversationDigest): string {
    const lines: string[] = ["<conversation_digest>"];

    if (digest.task) {
      lines.push(`  <current_task>${digest.task}</current_task>`);
    }

    if (digest.artifacts.length > 0) {
      lines.push("  <artifacts_from_last_response>");
      for (const a of digest.artifacts) {
        const label = a.label ? ` label="${a.label}"` : "";
        lines.push(`    <artifact type="${a.type}"${label}>${a.value}</artifact>`);
      }
      lines.push("  </artifacts_from_last_response>");
    }

    if (digest.decisions.length > 0) {
      lines.push("  <decisions_made>");
      for (const d of digest.decisions) {
        lines.push(`    <decision>${d}</decision>`);
      }
      lines.push("  </decisions_made>");
    }

    if (digest.failed.length > 0) {
      lines.push("  <already_tried_and_failed>");
      for (const f of digest.failed) {
        lines.push(`    <attempt>${f}</attempt>`);
      }
      lines.push("  </already_tried_and_failed>");
    }

    if (digest.openQuestions.length > 0) {
      lines.push("  <open_questions>");
      for (const q of digest.openQuestions) {
        lines.push(`    <question>${q}</question>`);
      }
      lines.push("  </open_questions>");
    }

    lines.push("</conversation_digest>");
    return lines.join("\n");
  }

  // ── Private helpers ──────────────────────────────────────────────

  private blank(sessionId: string): ConversationDigest {
    return {
      sessionId,
      task: "",
      artifacts: [],
      decisions: [],
      failed: [],
      openQuestions: [],
      updatedAt: new Date().toISOString(),
    };
  }

  private filePath(sessionId: string): string {
    // Sanitise session ID for use as filename
    const safe = sessionId.replace(/[^a-zA-Z0-9_-]/g, "_");
    return join(this.digestsDir, `${safe}.json`);
  }

  private extractArtifacts(
    text: string,
    existing: DigestArtifact[],
  ): DigestArtifact[] {
    const seen = new Set(existing.map((a) => a.value));
    const results: DigestArtifact[] = [...existing];

    // URLs
    for (const url of text.matchAll(URL_PATTERN)) {
      const value = url[0].replace(/[.,;:)>\]]+$/, ""); // trim trailing punctuation
      if (!seen.has(value) && value.length < 300) {
        seen.add(value);
        results.push({ type: "url", value });
      }
    }

    // Files written
    for (const match of text.matchAll(FILE_WRITTEN_PATTERN)) {
      const value = match[1];
      if (!seen.has(value)) {
        seen.add(value);
        results.push({ type: "file", value, label: "written" });
      }
    }

    // Shell commands run
    for (const match of text.matchAll(COMMAND_PATTERN)) {
      const value = match[1].trim();
      if (!seen.has(value)) {
        seen.add(value);
        results.push({ type: "command", value });
      }
    }

    return results.slice(-MAX_ARTIFACTS);
  }

  private extractDecisions(text: string, existing: string[]): string[] {
    const seen = new Set(existing.map((d) => d.toLowerCase()));
    const results = [...existing];

    for (const match of text.matchAll(DECISION_PATTERN)) {
      const value = match[1].trim().slice(0, MAX_LABEL_LEN);
      if (!seen.has(value.toLowerCase())) {
        seen.add(value.toLowerCase());
        results.push(value);
      }
    }

    return results.slice(-MAX_DECISIONS);
  }

  private extractFailures(text: string, existing: string[]): string[] {
    const seen = new Set(existing.map((f) => f.toLowerCase()));
    const results = [...existing];

    for (const match of text.matchAll(FAILURE_PATTERN)) {
      const value = match[1].trim().slice(0, MAX_LABEL_LEN);
      if (!seen.has(value.toLowerCase())) {
        seen.add(value.toLowerCase());
        results.push(value);
      }
    }

    return results.slice(-MAX_FAILED);
  }

  private extractOpenQuestions(text: string): string[] {
    const results: string[] = [];

    for (const match of text.matchAll(OPEN_Q_PATTERN)) {
      const value = match[0].trim().slice(0, MAX_LABEL_LEN);
      if (results.length < MAX_OPEN_Q) results.push(value);
    }

    return results;
  }
}
