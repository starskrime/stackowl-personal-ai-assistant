/**
 * StackOwl — Tool Profile Manager
 *
 * Manages tool access profiles similar to OpenCLAW.
 * Profiles define which tools are allowed/denied for different contexts.
 */

import type { ToolProfile, SandboxConfig } from "./types.js";

const DEFAULT_PROFILES: Record<string, ToolProfile> = {
  minimal: {
    name: "minimal",
    allowedTools: ["session_status"],
    deniedTools: [],
    sandboxMode: "always",
  },
  coding: {
    name: "coding",
    allowedTools: [
      "group:fs",
      "group:runtime",
      "group:sessions",
      "group:memory",
      "image",
    ],
    deniedTools: [],
    sandboxMode: "session",
  },
  messaging: {
    name: "messaging",
    allowedTools: [
      "group:messaging",
      "sessions_list",
      "sessions_history",
      "sessions_send",
      "session_status",
    ],
    deniedTools: [],
    sandboxMode: "session",
  },
  full: {
    name: "full",
    allowedTools: ["*"],
    deniedTools: [],
    sandboxMode: "none",
  },
};

export class ToolProfileManager {
  private profiles: Map<string, ToolProfile> = new Map();
  private activeProfile: string = "full";
  private customDenials: Set<string> = new Set();
  private customAllowances: Set<string> = new Set();
  private sandboxConfig: SandboxConfig = { enabled: false };

  constructor() {
    // Load default profiles
    for (const profile of Object.values(DEFAULT_PROFILES)) {
      this.profiles.set(profile.name, profile);
    }
  }

  /**
   * Set the active tool profile.
   */
  setProfile(name: string): boolean {
    if (this.profiles.has(name)) {
      this.activeProfile = name;
      return true;
    }
    return false;
  }

  /**
   * Get the active profile.
   */
  getActiveProfile(): ToolProfile | undefined {
    return this.profiles.get(this.activeProfile);
  }

  /**
   * Add custom allow/deny rules on top of profile.
   */
  addRules(allow: string[] = [], deny: string[] = []): void {
    for (const tool of allow) {
      this.customAllowances.add(tool);
      this.customDenials.delete(tool);
    }
    for (const tool of deny) {
      this.customDenials.add(tool);
      this.customAllowances.delete(tool);
    }
  }

  /**
   * Check if a tool is allowed.
   */
  isToolAllowed(toolName: string): boolean {
    const profile = this.getActiveProfile();
    if (!profile) return true;

    // Check custom denials first (they take precedence)
    if (this.customDenials.has(toolName)) return false;
    if (this.customDenials.has("*")) return false;

    // Check custom allowances
    if (this.customAllowances.has(toolName)) return true;
    if (this.customAllowances.has("*")) return true;

    // Check profile rules
    for (const allowed of profile.allowedTools) {
      if (this.matchTool(toolName, allowed)) return true;
    }

    for (const denied of profile.deniedTools) {
      if (this.matchTool(toolName, denied)) return false;
    }

    // Default allow if not explicitly denied
    return profile.deniedTools.length === 0;
  }

  /**
   * Match tool name against pattern (supports wildcards and groups).
   */
  private matchTool(toolName: string, pattern: string): boolean {
    // Exact match
    if (toolName === pattern) return true;

    // Wildcard
    if (pattern === "*") return true;

    // Group match (e.g., "group:fs")
    if (pattern.startsWith("group:")) {
      const group = pattern.slice(6);
      return this.isInGroup(toolName, group);
    }

    return false;
  }

  /**
   * Check if tool is in a group.
   */
  private isInGroup(toolName: string, group: string): boolean {
    const groups: Record<string, string[]> = {
      runtime: ["exec", "bash", "process"],
      fs: ["read", "write", "edit", "apply_patch"],
      sessions: [
        "sessions_list",
        "sessions_history",
        "sessions_send",
        "sessions_spawn",
        "session_status",
      ],
      memory: ["memory_search", "memory_get"],
      web: ["web_search", "web_fetch"],
      ui: ["browser", "canvas"],
      automation: ["cron", "gateway"],
      messaging: ["message"],
      nodes: ["nodes"],
    };

    const groupTools = groups[group] || [];
    return groupTools.includes(toolName);
  }

  /**
   * Configure sandbox.
   */
  setSandboxConfig(config: SandboxConfig): void {
    this.sandboxConfig = config;
  }

  /**
   * Get sandbox config.
   */
  getSandboxConfig(): SandboxConfig {
    return this.sandboxConfig;
  }

  /**
   * List available profiles.
   */
  listProfiles(): string[] {
    return Array.from(this.profiles.keys());
  }

  /**
   * Create a custom profile.
   */
  createProfile(profile: ToolProfile): void {
    this.profiles.set(profile.name, profile);
  }
}
