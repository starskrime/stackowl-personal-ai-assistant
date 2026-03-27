/**
 * StackOwl — Config Context Builder
 *
 * Builds a platform context snapshot for skill generation LLM prompts.
 * When the PatternMiner or SkillEvolver asks the LLM to generate or
 * rewrite a skill, this module injects:
 *
 *   1. Registered providers + models (what LLMs are available)
 *   2. Available tools from ToolRegistry (what the owl can actually do)
 *   3. Adapter capabilities (Telegram, Slack, MCP servers)
 *   4. Workspace layout (where files live)
 *   5. Existing skill names (to avoid overlap)
 *
 * Without this, generated skills are generic — they say "send a message"
 * instead of "use send_telegram_message() with the configured bot."
 */

import type { StackOwlConfig } from "../config/loader.js";
import type { ToolRegistry } from "../tools/registry.js";
import type { SkillsRegistry } from "./registry.js";
// logger reserved for future use

// ─── Types ───────────────────────────────────────────────────────

export interface PlatformSnapshot {
  /** Available LLM providers and their models */
  providers: string[];
  /** All registered tool names with descriptions */
  tools: Array<{ name: string; description: string }>;
  /** Configured communication adapters */
  adapters: string[];
  /** MCP server names */
  mcpServers: string[];
  /** Existing skill names (to avoid overlap) */
  existingSkills: string[];
  /** Workspace base path */
  workspacePath: string;
  /** Key config flags */
  capabilities: string[];
}

// ─── Builder ─────────────────────────────────────────────────────

export class ConfigContextBuilder {
  constructor(
    private config: StackOwlConfig,
    private toolRegistry?: ToolRegistry,
    private skillsRegistry?: SkillsRegistry,
  ) {}

  /**
   * Build a full platform snapshot. Call once per crystallization pass.
   */
  build(): PlatformSnapshot {
    return {
      providers: this.getProviders(),
      tools: this.getTools(),
      adapters: this.getAdapters(),
      mcpServers: this.getMCPServers(),
      existingSkills: this.getExistingSkills(),
      workspacePath: this.config.workspace,
      capabilities: this.getCapabilities(),
    };
  }

  /**
   * Format the snapshot as an LLM-injectable context block.
   * Designed to be appended to skill generation/rewrite prompts.
   */
  toPromptBlock(snapshot?: PlatformSnapshot): string {
    const s = snapshot ?? this.build();
    const lines: string[] = [
      "PLATFORM CONTEXT (your actual runtime environment):",
      "",
    ];

    // Providers
    if (s.providers.length > 0) {
      lines.push(`Available LLM providers: ${s.providers.join(", ")}`);
    }

    // Tools — the most critical section
    if (s.tools.length > 0) {
      lines.push("");
      lines.push(
        "Available tools (use EXACTLY these names in skill instructions):",
      );
      for (const tool of s.tools) {
        lines.push(`  - ${tool.name}: ${tool.description}`);
      }
    }

    // Adapters
    if (s.adapters.length > 0) {
      lines.push("");
      lines.push(`Communication adapters: ${s.adapters.join(", ")}`);
      if (s.adapters.includes("telegram")) {
        lines.push(
          "  → For Telegram: use send_telegram_message(text) and send_file(path) tools",
        );
      }
      if (s.adapters.includes("slack")) {
        lines.push("  → For Slack: use send_slack_message(channel, text) tool");
      }
    }

    // MCP servers
    if (s.mcpServers.length > 0) {
      lines.push("");
      lines.push(`MCP servers: ${s.mcpServers.join(", ")}`);
      lines.push(
        "  → These provide additional tool capabilities via MCP protocol",
      );
    }

    // Capabilities
    if (s.capabilities.length > 0) {
      lines.push("");
      lines.push(`Platform capabilities: ${s.capabilities.join(", ")}`);
    }

    // Existing skills
    if (s.existingSkills.length > 0) {
      lines.push("");
      lines.push(
        `Existing skills (do NOT duplicate): ${s.existingSkills.join(", ")}`,
      );
    }

    // Workspace
    lines.push("");
    lines.push(`Workspace path: ${s.workspacePath}`);

    lines.push("");
    lines.push(
      "IMPORTANT: Generated skills MUST reference the actual tool names listed above. " +
        "Do NOT invent tool names. If the skill needs to send a message via Telegram, " +
        'use "send_telegram_message", not "send_message" or "telegram_send".',
    );

    return lines.join("\n");
  }

  // ─── Private Extractors ────────────────────────────────────────

  private getProviders(): string[] {
    return Object.entries(this.config.providers).map(([name, entry]) => {
      const model = entry.defaultModel ?? "default";
      return `${name} (${model})`;
    });
  }

  private getTools(): PlatformSnapshot["tools"] {
    if (!this.toolRegistry) return [];

    try {
      const defs = this.toolRegistry.getAllDefinitions();
      return defs.map((d) => ({
        name: d.name,
        description: (d.description ?? "").slice(0, 100),
      }));
    } catch {
      return [];
    }
  }

  private getAdapters(): string[] {
    const adapters: string[] = [];

    if (this.config.telegram?.botToken) {
      adapters.push("telegram");
    }
    if (this.config.slack?.botToken) {
      adapters.push("slack");
    }

    return adapters;
  }

  private getMCPServers(): string[] {
    if (!this.config.mcp?.servers) return [];
    return this.config.mcp.servers.map((s) => s.name);
  }

  private getExistingSkills(): string[] {
    if (!this.skillsRegistry) return [];

    try {
      return this.skillsRegistry.listEnabled().map((s) => s.name);
    } catch {
      return [];
    }
  }

  private getCapabilities(): string[] {
    const caps: string[] = [];

    if (this.config.execution?.hostMode) caps.push("host_shell_access");
    if (this.config.execution?.sandboxMode) caps.push("sandboxed_execution");
    if (this.config.browser?.enabled !== false) caps.push("browser_pool");
    if (this.config.skills?.enabled) caps.push("skill_system");
    if (this.config.owlDna?.enabled) caps.push("dna_evolution");
    if (this.config.smartRouting?.enabled) caps.push("smart_routing");
    if (this.config.costs?.enabled) caps.push("cost_tracking");
    if (this.config.storage?.backend === "sqlite") caps.push("sqlite_storage");

    return caps;
  }
}
