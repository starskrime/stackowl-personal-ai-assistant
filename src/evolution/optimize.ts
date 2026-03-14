/**
 * StackOwl — Evolution Optimization Module
 *
 * Contains optimizations for the evolution system to address hard-coded dependencies
 * and inefficient process handling.
 */

import { log } from "../logger.js";

/**
 * Optimizes the evolution process by:
 * 1. Sampling conversation history instead of full processing
 * 2. Adding better dependency management
 * 3. Reducing memory usage for long sessions
 */
export class EvolutionOptimizer {
  /**
   * Sample conversation messages to avoid processing very long sessions
   * and to improve performance and reduce context size
   */
  static sampleSessionMessages(
    messages: any[],
    maxMessages: number = 12,
  ): any[] {
    // If conversation is too long, take only the last maxMessages turns
    if (messages.length > maxMessages) {
      const userAssistantMessages = messages.filter(
        (m) => m.role === "user" || m.role === "assistant",
      );
      return userAssistantMessages.slice(-maxMessages);
    }

    // Filter just user/assistant messages
    return messages.filter((m) => m.role === "user" || m.role === "assistant");
  }

  /**
   * Check if system has required dependencies to handle capability gaps
   */
  static async checkDependencies(): Promise<{
    hasAll: boolean;
    missing: string[];
  }> {
    const missing: string[] = [];

    // Check for common requirements
    try {
      // For now, check basic Node.js capabilities
      require("node:fs");
      require("node:path");
      require("node:child_process");
    } catch (err) {
      missing.push("core Node modules");
    }

    // Check if platform-specific tools are available
    // We can add more checks based on actual requirements

    return {
      hasAll: missing.length === 0,
      missing,
    };
  }

  /**
   * Provides platform-specific configuration for evolution
   */
  static getPlatformConfig(): {
    isSupported: boolean;
    recommendedActions: string[];
  } {
    const platform = process.platform;
    const isSupported = ["darwin", "linux", "win32"].includes(platform);

    const recommendedActions = [];
    if (!isSupported) {
      recommendedActions.push(
        "Platform not recognized, limited functionality available",
      );
    }

    return {
      isSupported,
      recommendedActions,
    };
  }

  /**
   * Memory-efficient evolution analysis that caps resource usage
   */
  static async analyzeSessionEfficiently(
    session: any,
    maxMessages: number = 12,
  ): Promise<{ analysis: string; messageCount: number }> {
    log.evolution.debug("Performing memory-efficient session analysis");

    // Sample messages instead of processing entire history
    const sampledMessages = this.sampleSessionMessages(
      session.messages,
      maxMessages,
    );

    // Create a minimal transcript for analysis
    const transcript = sampledMessages
      .map(
        (m: any) =>
          `[${m.role.toUpperCase()}]: ${(m.content ?? "").slice(0, 400)}`,
      )
      .join("\n\n");

    return {
      analysis: transcript,
      messageCount: sampledMessages.length,
    };
  }
}
