/**
 * StackOwl — Platform Capability Scanner
 *
 * Scans the platform config and tool registry to identify:
 *   1. Configured but UNUSED adapters (Telegram set up but never messaged)
 *   2. Available tools with no matching skill
 *   3. MCP servers the owl hasn't leveraged
 *   4. Skill gaps based on user's top topics
 *   5. Tool permission issues (tools exist but denied)
 *
 * Produces actionable suggestions the idle engine or planner can act on.
 */

import type { StackOwlConfig } from "../config/loader.js";
import type { ToolRegistry } from "../tools/registry.js";
import type { SkillsRegistry } from "../skills/registry.js";
import type { MicroLearner } from "../learning/micro-learner.js";
import { log } from "../logger.js";

// ─── Types ───────────────────────────────────────────────────────

export interface CapabilityGap {
  type:
    | "unused_adapter"
    | "tool_without_skill"
    | "unused_mcp"
    | "topic_gap"
    | "permission_gap";
  name: string;
  description: string;
  /** Suggested action to fill this gap */
  suggestion: string;
  /** Priority 0-100 */
  priority: number;
}

export interface ScanResult {
  gaps: CapabilityGap[];
  totalToolsRegistered: number;
  totalSkillsEnabled: number;
  coveragePercent: number;
  timestamp: string;
}

// ─── Scanner ─────────────────────────────────────────────────────

export class CapabilityScanner {
  constructor(
    private config: StackOwlConfig,
    private toolRegistry?: ToolRegistry,
    private skillsRegistry?: SkillsRegistry,
    private microLearner?: MicroLearner,
  ) {}

  /**
   * Run a full capability scan.
   * Returns all identified gaps sorted by priority.
   */
  scan(): ScanResult {
    const gaps: CapabilityGap[] = [];

    gaps.push(...this.scanUnusedAdapters());
    gaps.push(...this.scanToolsWithoutSkills());
    gaps.push(...this.scanUnusedMCP());
    gaps.push(...this.scanTopicGaps());
    gaps.push(...this.scanPermissionGaps());

    // Sort by priority (highest first)
    gaps.sort((a, b) => b.priority - a.priority);

    const totalTools = this.toolRegistry?.getAllDefinitions().length ?? 0;
    const totalSkills = this.skillsRegistry?.listEnabled().length ?? 0;
    const coverage =
      totalTools > 0
        ? Math.min(100, Math.round((totalSkills / totalTools) * 100))
        : 0;

    log.engine.info(
      `[CapabilityScanner] Scan complete: ${gaps.length} gaps found, ` +
        `${totalTools} tools, ${totalSkills} skills, ${coverage}% coverage`,
    );

    return {
      gaps,
      totalToolsRegistered: totalTools,
      totalSkillsEnabled: totalSkills,
      coveragePercent: coverage,
      timestamp: new Date().toISOString(),
    };
  }

  /**
   * Get the top N most actionable gaps.
   */
  getTopGaps(limit: number = 5): CapabilityGap[] {
    return this.scan().gaps.slice(0, limit);
  }

  /**
   * Format scan results as a prompt for the idle engine.
   */
  toIdlePrompt(result?: ScanResult): string {
    const r = result ?? this.scan();
    if (r.gaps.length === 0) return "";

    const top = r.gaps.slice(0, 5);
    const lines: string[] = [
      "CAPABILITY GAPS (things you could improve right now):",
    ];

    for (const gap of top) {
      lines.push(`- [${gap.type}] ${gap.name}: ${gap.suggestion}`);
    }

    return lines.join("\n");
  }

  // ─── Sub-Scanners ──────────────────────────────────────────────

  private scanUnusedAdapters(): CapabilityGap[] {
    const gaps: CapabilityGap[] = [];

    // Check Telegram
    if (this.config.telegram?.botToken && this.microLearner) {
      const profile = this.microLearner.getProfile();
      const telegramUsage = profile.toolUsage["send_telegram_message"] ?? 0;
      if (telegramUsage === 0 && profile.totalMessages > 10) {
        gaps.push({
          type: "unused_adapter",
          name: "telegram",
          description:
            "Telegram is configured but the user has never received a proactive message",
          suggestion:
            "Create a skill that sends daily summaries or reminders via Telegram",
          priority: 75,
        });
      }
    }

    // Check Slack
    if (this.config.slack?.botToken && this.microLearner) {
      const profile = this.microLearner.getProfile();
      const slackUsage = profile.toolUsage["send_slack_message"] ?? 0;
      if (slackUsage === 0 && profile.totalMessages > 10) {
        gaps.push({
          type: "unused_adapter",
          name: "slack",
          description: "Slack is configured but never used for messaging",
          suggestion:
            "Create a skill that posts status updates or alerts to Slack channels",
          priority: 65,
        });
      }
    }

    return gaps;
  }

  private scanToolsWithoutSkills(): CapabilityGap[] {
    if (!this.toolRegistry || !this.skillsRegistry) return [];

    const gaps: CapabilityGap[] = [];
    const toolDefs = this.toolRegistry.getAllDefinitions();
    const skillDescriptions = this.skillsRegistry
      .listEnabled()
      .map((s) => `${s.name} ${s.description}`.toLowerCase());

    // Core tools that should have skills
    const importantTools = [
      "web_crawl",
      "google_search",
      "generate_image",
      "send_telegram_message",
      "send_file",
      "read_file",
      "write_file",
    ];

    for (const tool of toolDefs) {
      // Skip if a skill already covers this tool
      const toolLower = tool.name.toLowerCase();
      const hasCoverage = skillDescriptions.some(
        (d) =>
          d.includes(toolLower) || d.includes(toolLower.replace(/_/g, " ")),
      );
      if (hasCoverage) continue;

      // Only flag important tools or frequently used ones
      const isImportant = importantTools.includes(tool.name);
      if (!isImportant) continue;

      gaps.push({
        type: "tool_without_skill",
        name: tool.name,
        description: `Tool "${tool.name}" has no matching skill — the owl uses raw tool calls instead of optimized skill steps`,
        suggestion: `Create a skill that wraps "${tool.name}" with best-practice steps and error handling`,
        priority: isImportant ? 55 : 30,
      });
    }

    return gaps;
  }

  private scanUnusedMCP(): CapabilityGap[] {
    if (!this.config.mcp?.servers) return [];

    const gaps: CapabilityGap[] = [];
    for (const server of this.config.mcp.servers) {
      // Check if any tool from this MCP server has been used
      // (MCP tools are prefixed with the server name)
      if (this.microLearner) {
        const profile = this.microLearner.getProfile();
        const serverTools = Object.keys(profile.toolUsage).filter((t) =>
          t.toLowerCase().includes(server.name.toLowerCase()),
        );
        if (serverTools.length === 0 && profile.totalMessages > 10) {
          gaps.push({
            type: "unused_mcp",
            name: server.name,
            description: `MCP server "${server.name}" is configured but its tools have never been used`,
            suggestion: `Research what "${server.name}" MCP server provides and create skills that leverage its capabilities`,
            priority: 50,
          });
        }
      }
    }

    return gaps;
  }

  private scanTopicGaps(): CapabilityGap[] {
    if (!this.microLearner) return [];

    const gaps: CapabilityGap[] = [];
    const anticipated = this.microLearner.getAnticipatedNeeds();

    for (const need of anticipated.slice(0, 3)) {
      if (need.confidence > 0.5) {
        gaps.push({
          type: "topic_gap",
          name: need.capability,
          description: need.reason,
          suggestion: `Research "${need.capability}" and create a skill or store knowledge pellets about it`,
          priority: Math.round(need.confidence * 60),
        });
      }
    }

    return gaps;
  }

  private scanPermissionGaps(): CapabilityGap[] {
    if (!this.config.tools?.permissions) return [];

    const gaps: CapabilityGap[] = [];
    for (const [tool, permission] of Object.entries(
      this.config.tools.permissions,
    )) {
      if (permission === "denied") {
        gaps.push({
          type: "permission_gap",
          name: tool,
          description: `Tool "${tool}" exists but is denied by permissions config`,
          suggestion: `Consider whether "${tool}" should be enabled — the user might benefit from it`,
          priority: 20,
        });
      }
    }

    return gaps;
  }
}
