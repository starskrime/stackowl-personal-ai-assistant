/**
 * StackOwl — Tool Framework Base & Registry
 *
 * Manages registration, validation, permission gating, and execution
 * of available tools for the Owl Engine.
 */

import type { ToolDefinition } from '../providers/base.js';
import type { EngineContext } from '../engine/runtime.js';
import type { ToolCategory, ToolPermission } from './categories.js';
import { DEFAULT_PERMISSIONS } from './categories.js';
import { validateToolArgs } from './validator.js';
import {
    ToolNotFoundError,
    ToolValidationError,
    ToolPermissionError,
    ToolExecutionError,
} from './errors.js';

export type { ToolDefinition };

export interface ToolContext {
    cwd: string;
    engineContext?: EngineContext;
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
    private permissions: Record<string, ToolPermission> = { ...DEFAULT_PERMISSIONS };

    /**
     * Register a new tool.
     */
    register(tool: ToolImplementation): void {
        this.tools.set(tool.definition.name, tool);
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
        return this.tools.delete(name);
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
     * Get all registered tool definitions for the LLM.
     */
    getDefinitions(): ToolDefinition[] {
        return Array.from(this.tools.values())
            .filter(t => this.checkPermission(t) === 'allowed')
            .map(t => t.definition);
    }

    /**
     * Get tool definitions grouped by category.
     */
    getDefinitionsByCategory(): Map<ToolCategory | 'uncategorized', ToolDefinition[]> {
        const map = new Map<ToolCategory | 'uncategorized', ToolDefinition[]>();
        for (const tool of this.tools.values()) {
            const cat = tool.category ?? 'uncategorized';
            if (!map.has(cat)) map.set(cat, []);
            map.get(cat)!.push(tool.definition);
        }
        return map;
    }

    /**
     * Get tools by category.
     */
    getByCategory(category: ToolCategory): ToolImplementation[] {
        return Array.from(this.tools.values()).filter(t => t.category === category);
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
        return Array.from(this.tools.values()).map(t => ({
            name: t.definition.name,
            category: t.category,
            source: t.source,
        }));
    }

    /**
     * Execute a tool by name with arguments.
     * Validates args against schema, checks permissions, truncates long results.
     */
    async execute(name: string, args: Record<string, unknown>, context: ToolContext): Promise<string> {
        const tool = this.tools.get(name);
        if (!tool) {
            throw new ToolNotFoundError(name);
        }

        // Permission check
        const perm = this.checkPermission(tool);
        if (perm === 'denied') {
            throw new ToolPermissionError(name, tool.category ?? 'uncategorized');
        }

        // Schema validation
        const violations = validateToolArgs(
            tool.definition.parameters as Record<string, unknown> | undefined,
            args,
        );
        if (violations.length > 0) {
            throw new ToolValidationError(name, violations);
        }

        try {
            let result = await tool.execute(args, context);

            // Truncate long results to prevent context bloat
            if (result.length > MAX_TOOL_RESULT_LENGTH) {
                result =
                    result.slice(0, MAX_TOOL_RESULT_LENGTH) +
                    `\n\n[OUTPUT TRUNCATED — ${result.length} chars total, showing first ${MAX_TOOL_RESULT_LENGTH}]`;
            }

            return result;
        } catch (error) {
            if (error instanceof ToolExecutionError) throw error;
            const msg = error instanceof Error ? error.message : String(error);
            throw new ToolExecutionError(name, msg);
        }
    }

    private checkPermission(tool: ToolImplementation): ToolPermission {
        if (!tool.category) return 'allowed';
        return this.permissions[tool.category] ?? 'allowed';
    }
}
