/**
 * StackOwl — Tool Framework Base & Registry
 *
 * Manages registration, validation, permission gating, and execution
 * of available tools for the Owl Engine.
 */

import type { ToolDefinition } from "../providers/base.js";
import { log } from "../logger.js";
import { withSpan } from "../infra/observability/context.js";
import { SemanticToolGate } from "../intelligence/semantic-tool-gate.js";
import type { EngineContext } from "../engine/runtime.js";
import type { GatewayEventBus } from "../gateway/event-bus.js";
import type { ToolCategory, ToolPermission } from "./categories.js";
import type { GoalVerifier } from "./goal-verifier.js";
import { DEFAULT_PERMISSIONS } from "./categories.js";
import { validateToolArgs } from "./validator.js";
import {
  ToolNotFoundError,
  ToolValidationError,
  ToolPermissionError,
  ToolExecutionError,
} from "./errors.js";
import type { ToolIntentRouter } from "./intent-router.js";
import type { ToolTracker } from "./tracker.js";
import type { BlockingClassifier } from "../browser/blocking-classifier.js";
import { platform } from "../platform/index.js";

export type { ToolDefinition };

export interface ToolContext {
  cwd: string;
  /** Absolute path to the synthesized tools directory. Used by patch_tool and other
   *  tools that need to locate synthesized tool files. Falls back to the legacy
   *  source-tree constant when not provided. */
  synthesizedDir?: string;
  engineContext?: EngineContext;
  classifier?: Pick<BlockingClassifier, "classify">;
  puppeteer?: import("../browser/puppeteer-fetcher.js").PuppeteerFetcher;
  camofox?: import("../browser/camofox-client.js").CamoFoxClient;
  tavilyApiKey?: string;
}

export interface ToolImplementation {
  /** The definition sent to the LLM */
  definition: ToolDefinition;
  /** Tool category for permission gating */
  category?: ToolCategory;
  /** Source of this tool: 'builtin' | 'synthesized' | 'mcp' | 'skill' */
  source?: string;
  /** The actual execution logic */
  execute(args: Record<string, unknown>, context: ToolContext): Promise<string>;
}

/** Maximum characters per tool result before truncation */
const MAX_TOOL_RESULT_LENGTH = 6000;

export class ToolRegistry {
  private tools: Map<string, ToolImplementation> = new Map();
  private permissions: Record<string, ToolPermission> = {
    ...DEFAULT_PERMISSIONS,
  };
  private _intentRouter: ToolIntentRouter | null = null;
  private _tracker: ToolTracker | null = null;
  private _eventBus: GatewayEventBus | null = null;
  private _goalVerifier: GoalVerifier | null = null;
  private _riskGuard: import('../clarification/tool-risk-guard.js').ToolRiskGuard | null = null;
  private _toolGraph: import('./cortex/tool-graph.js').ToolGraph | null = null;
  private _edgeAccumulator: import('./cortex/edge-accumulator.js').EdgeAccumulator | null = null;
  private _semanticGate = new SemanticToolGate();
  private _gateIndexed = false;

  setIntentRouter(router: ToolIntentRouter): void {
    this._intentRouter = router;
    this.reindexTools();
  }

  setTracker(tracker: ToolTracker): void {
    this._tracker = tracker;
  }

  setEventBus(bus: GatewayEventBus): void {
    this._eventBus = bus;
  }

  setGoalVerifier(verifier: GoalVerifier): void {
    this._goalVerifier = verifier;
  }

  setRiskGuard(guard: import('../clarification/tool-risk-guard.js').ToolRiskGuard): void {
    this._riskGuard = guard;
  }

  setToolGraph(g: import('./cortex/tool-graph.js').ToolGraph): void {
    this._toolGraph = g;
  }

  setEdgeAccumulator(a: import('./cortex/edge-accumulator.js').EdgeAccumulator): void {
    this._edgeAccumulator = a;
  }

  getTracker(): ToolTracker | null {
    return this._tracker;
  }

  getIntentRouter(): ToolIntentRouter | null {
    return this._intentRouter;
  }

  /** Re-index tools in the intent router (call after tool register/unregister) */
  reindexTools(): void {
    if (!this._intentRouter) return;
    const defs = Array.from(this.tools.values())
      .filter((t) => this.checkPermission(t) === "allowed")
      .map((t) => t.definition);
    this._intentRouter.reindex(defs);
  }

  /**
   * Register a new tool. (Prevents overwriting existing tools to secure against spoofing).
   */
  register(tool: ToolImplementation): void {
    if (this.tools.has(tool.definition.name)) {
      throw new Error(
        `Tool collision: A tool named '${tool.definition.name}' is already registered.`,
      );
    }
    this.tools.set(tool.definition.name, tool);
    this.invalidateGateIndex();
  }

  /**
   * Register multiple tools at once.
   */
  registerAll(tools: ToolImplementation[]): void {
    for (const tool of tools) {
      this.register(tool);
    }
  }

  /**
   * Remove a tool from the registry (used for MCP disconnect).
   */
  unregister(name: string): boolean {
    const deleted = this.tools.delete(name);
    if (deleted) this.invalidateGateIndex();
    return deleted;
  }

  /**
   * Set permission level for a tool category.
   */
  setPermission(category: ToolCategory, permission: ToolPermission): void {
    this.permissions[category] = permission;
  }

  /**
   * Load permissions from config.
   */
  loadPermissions(perms: Record<string, ToolPermission>): void {
    for (const [cat, perm] of Object.entries(perms)) {
      this.permissions[cat] = perm;
    }
  }

  /**
   * Get a single tool definition by name (allowed or deprecated).
   * Returns undefined if the tool is not registered.
   * Used by quality-checklist tests and the cortex (CWTG/SET) for metadata
   * lookup — including deprecated tools so historical edges remain queryable.
   */
  getDefinition(name: string): ToolDefinition | undefined {
    return this.tools.get(name)?.definition;
  }

  /**
   * Get a snapshot Map of all allowed, non-deprecated tool implementations.
   * Used by SubOwlRunner to dispatch tool calls inside sub-owl ReAct loops.
   */
  getImplementationsMap(): Map<string, ToolImplementation> {
    const out = new Map<string, ToolImplementation>();
    for (const [name, impl] of this.tools.entries()) {
      if (
        this.checkPermission(impl) === "allowed" &&
        !impl.definition.deprecated
      ) {
        out.set(name, impl);
      }
    }
    return out;
  }

  /**
   * Get ALL allowed tool definitions synchronously (no routing).
   * Use for: history sanitization, capability checks, admin operations.
   */
  getAllDefinitions(): ToolDefinition[] {
    return Array.from(this.tools.values())
      .filter((t) => this.checkPermission(t) === "allowed")
      .filter((t) => !t.definition.deprecated)
      .map((t) => t.definition);
  }

  /**
   * Get the top-K most relevant tools for a given query using SemanticToolGate.
   * Falls back to returning all tools (up to limit) when no embed function is
   * configured (i.e. gate was indexed without an embedFn).
   */
  async getRelevantTools(query: string, limit = 8): Promise<ToolDefinition[]> {
    const allDefs = this.getAllDefinitions();
    if (!this._gateIndexed) {
      await this._semanticGate.index(allDefs);
      this._gateIndexed = true;
    }
    return this._semanticGate.getRelevant(query, limit);
  }

  /**
   * Invalidate the SemanticToolGate index so it is rebuilt on next call to
   * getRelevantTools(). Called automatically on register() and unregister().
   */
  invalidateGateIndex(): void {
    this._gateIndexed = false;
  }

  /**
   * Get tool definitions for the LLM.
   * - Without options: returns all allowed tools (backwards compatible).
   * - With options.userMessage: uses ToolIntentRouter for per-turn intelligent selection.
   */
  async getDefinitions(options?: {
    maxTools?: number;
    userMessage?: string;
  }): Promise<ToolDefinition[]> {
    if (!options?.userMessage || !this._intentRouter) {
      return this.getAllDefinitions();
    }

    const matches = await this._intentRouter.route(
      options.userMessage,
      options.maxTools ?? 8,
    );

    return matches.map((m) => m.definition);
  }

  /**
   * Get tool definitions grouped by category.
   */
  getDefinitionsByCategory(): Map<
    ToolCategory | "uncategorized",
    ToolDefinition[]
  > {
    const map = new Map<ToolCategory | "uncategorized", ToolDefinition[]>();
    for (const tool of this.tools.values()) {
      const cat = tool.category ?? "uncategorized";
      if (!map.has(cat)) map.set(cat, []);
      map.get(cat)!.push(tool.definition);
    }
    return map;
  }

  /**
   * Get tools by category.
   */
  getByCategory(category: ToolCategory): ToolImplementation[] {
    return Array.from(this.tools.values()).filter(
      (t) => t.category === category,
    );
  }

  /**
   * Check if a tool is registered.
   */
  has(name: string): boolean {
    return this.tools.has(name);
  }

  /**
   * List all tools with metadata.
   */
  listAll(): { name: string; category?: string; source?: string }[] {
    return Array.from(this.tools.values()).map((t) => ({
      name: t.definition.name,
      category: t.category,
      source: t.source,
    }));
  }

  /**
   * Get tools by source (e.g. 'plugin', 'mcp', 'synthesized', 'builtin').
   * Used for plugin cleanup on unload.
   */
  getBySource(source: string): ToolImplementation[] {
    return Array.from(this.tools.values()).filter((t) => t.source === source);
  }

  /**
   * Execute a tool by name with arguments.
   * Validates args against schema, checks permissions, truncates long results.
   */
  async execute(
    name: string,
    args: Record<string, unknown>,
    context: ToolContext,
    /** Internal: recursion depth for ToolGraph single-hop replan. Capped at 1. */
    _replanDepth = 0,
    /** Optional mutable sink — caller reads verdict after execute() returns. */
    _verdictSink?: { verdict?: string; reason?: string },
  ): Promise<string> {
    const tool = this.tools.get(name);
    if (!tool) {
      throw new ToolNotFoundError(name);
    }

    // Permission check
    const perm = this.checkPermission(tool);
    if (perm === "denied") {
      throw new ToolPermissionError(name, tool.category ?? "uncategorized");
    }

    // Platform enforcement
    // Platform-blocked calls are intentionally not emitted to the event bus — no execution occurred.
    if (tool.definition.platforms && !tool.definition.platforms.includes(platform.systemInfo.current().platform as NodeJS.Platform)) {
      return JSON.stringify({
        success: false,
        data: null,
        error: {
          code: "PLATFORM_NOT_SUPPORTED",
          message: `Tool '${name}' is only available on: ${tool.definition.platforms.join(", ")}. Current platform: ${platform.systemInfo.current().platform}.`,
          suggestion: "Use a cross-platform alternative or run on a supported OS.",
        },
      });
    }

    // Schema validation
    const violations = validateToolArgs(
      tool.definition.parameters as Record<string, unknown> | undefined,
      args,
    );
    if (violations.length > 0) {
      throw new ToolValidationError(name, violations);
    }

    // Risk guard — Mode B pre-action check (fires after schema validation, before execution)
    if (this._riskGuard) {
      const riskResult = await this._riskGuard.check(name, args, (tool.definition.executionPolicy ?? {}) as Record<string, unknown>);
      if (!riskResult.allowed) {
        return riskResult.userFacingMessage;
      }
    }

    const startTime = Date.now();
    this._eventBus?.emit({ type: "tool:start", toolName: name, args, turnId: context.engineContext?.sessionId ?? "" });

    // Sanitize args before logging — mask sensitive keys
    const sanitizedArgs: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(args)) {
      const lk = k.toLowerCase();
      sanitizedArgs[k] = (lk === "apikey" || lk === "token" || lk === "password" || lk === "secret" ||
        lk.endsWith("_key") || lk.startsWith("key_") || lk.endsWith("token") || lk.endsWith("secret"))
        ? "[REDACTED]"
        : v;
    }

    return withSpan("tool.exec", async () => {
      log.tool.toolCall(name, sanitizedArgs);

      try {
        let result = await tool.execute(args, context);
        const durationMs = Date.now() - startTime;

        if (this._tracker) {
          this._tracker.recordSuccess(name, durationMs, {
            sessionId: context.engineContext?.sessionId,
          });
        }

        // Truncate long results to prevent context bloat
        let truncated = false;
        if (result.length > MAX_TOOL_RESULT_LENGTH) {
          result =
            result.slice(0, MAX_TOOL_RESULT_LENGTH) +
            `\n\n[OUTPUT TRUNCATED — ${result.length} chars total, showing first ${MAX_TOOL_RESULT_LENGTH}]`;
          truncated = true;
        }

        this._eventBus?.emit({ type: "tool:result", toolName: name, success: true, durationMs, truncated });

        // Envelope passthrough — emit <tool_attempt_summary> regardless of GAV
        try {
          const { parseWebToolResult, buildAttemptSummaryXml } = await import("../browser/envelope.js");
          const env = parseWebToolResult(result);
          if (env && !env.success && !result.includes("<tool_attempt_summary")) {
            result = result + "\n\n" + buildAttemptSummaryXml(env);
          }
        } catch { /* envelope parse is best-effort */ }

        // GAV: verify result against active sub-goal (skip if no sub-goal or no verifier)
        if (this._goalVerifier && context.engineContext?.activeSubGoal) {
          const subGoal = context.engineContext.activeSubGoal;
          const userMessage = context.engineContext.userMessage ?? "";
          try {
            const verification = await this._goalVerifier.verify({
              toolName: name,
              toolArgs: args,
              toolResult: result,
              subGoal,
              userMessage,
            });

            if (verification.verdict === "ADVANCES" || verification.verdict === "PARTIAL") {
              this._eventBus?.emit({
                type: "tool:goal_advance",
                toolName: name,
                subGoal: subGoal.description,
                verdict: verification.verdict,
              });
            }

            if (verification.verdict === "BLOCKED") {
              this._eventBus?.emit({
                type: "tool:goal_blocked",
                toolName: name,
                subGoal: subGoal.description,
                suggestion: verification.suggestion,
              });

              // CWTG single-hop replan: if a ToolGraph is configured, the failing
              // tool advertises a capability tag, and we haven't already taken a
              // fallback hop, ask the graph for the next-best alternative and
              // execute it. The graph's edge filter excludes the failing tool;
              // the depth cap prevents the recursive call from re-replanning.
              const capability = tool.definition.capabilities?.[0];
              if (this._toolGraph && capability && _replanDepth === 0) {
                const urlHost = (() => {
                  try {
                    return args.url ? new URL(args.url as string).hostname : "";
                  } catch (err) {
                    log.tool.warn("registry: failed to parse tool URL for replanning", err);
                    return "";
                  }
                })();
                const fallback = this._toolGraph.replan(name, capability, { hostRoot: urlHost });
                if (fallback && this.tools.has(fallback)) {
                  this._eventBus?.emit({
                    type: "tool:fallback",
                    fromTool: name,
                    toTool: fallback,
                    reason: verification.reason,
                  });
                  const fallbackStart = Date.now();
                  const fallbackResult = await this.execute(
                    fallback,
                    args,
                    context,
                    _replanDepth + 1,
                  );
                  this._edgeAccumulator?.observe({
                    fromTool: name,
                    toTool: fallback,
                    capabilityTag: capability,
                    success: true,
                    durationMs: Date.now() - fallbackStart,
                  });
                  return fallbackResult;
                }
              }
            }

            // Surface verdict to caller for trajectory recording.
            if (_verdictSink) {
              _verdictSink.verdict = verification.verdict;
              _verdictSink.reason = verification.reason;
            }

            // Envelope-aware: web tools return JSON-stringified WebToolResult.
            // For envelope errors, the unconditional passthrough above already
            // appended <tool_attempt_summary>; only fall back to the legacy
            // <tool_result_warning> for non-envelope tools.
            const { parseWebToolResult } = await import("../browser/envelope.js");
            const envelope = parseWebToolResult(result);
            if (!envelope && (verification.verdict === "BLOCKED" || verification.verdict === "PARTIAL")) {
              result = result + `\n\n<tool_result_warning verdict="${verification.verdict}">${verification.reason}${verification.suggestion ? ` Suggestion: ${verification.suggestion}` : ""}</tool_result_warning>`;
            }
          } catch (err) {
            // Verifier failure is non-fatal — proceed with unmodified result
            log.tool.warn("registry: tool verifier failed (non-fatal)", err);
          }
        }

        log.tool.toolResult(name, result, true);
        return result;
      } catch (error) {
        const durationMs = Date.now() - startTime;
        const errorMessage = error instanceof Error ? error.message : String(error);
        const errorCode =
          error instanceof ToolExecutionError
            ? "EXEC_FAILED"
            : error instanceof Error
              ? error.constructor.name
              : "UNKNOWN";
        if (this._tracker) {
          this._tracker.recordFailure(name, durationMs, {
            errorCode,
            errorMessage,
            sessionId: context.engineContext?.sessionId,
          });
        }
        this._eventBus?.emit({ type: "tool:result", toolName: name, success: false, durationMs, truncated: false });
        log.tool.error("tool.exec failed", error, { tool: name, args: sanitizedArgs, durationMs });
        if (error instanceof ToolExecutionError) throw error;
        throw new ToolExecutionError(name, errorMessage);
      }
    }, { tool: name });
  }

  private checkPermission(tool: ToolImplementation): ToolPermission {
    if (!tool.category) return "allowed";
    return this.permissions[tool.category] ?? "allowed";
  }
}
