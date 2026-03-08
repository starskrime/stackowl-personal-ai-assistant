/**
 * StackOwl — Tool Framework Base & Registry
 *
 * Defines the tool execution interface and manages registration
 * of available tools for the Owl Engine.
 */

import type { ToolDefinition } from '../providers/base.js';

export interface ToolContext {
    cwd: string;
}

export interface ToolImplementation {
    /** The definition sent to the LLM */
    definition: ToolDefinition;
    /** The actual execution logic */
    execute(args: Record<string, unknown>, context: ToolContext): Promise<string>;
}

export class ToolRegistry {
    private tools: Map<string, ToolImplementation> = new Map();

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
     * Get all registered tool definitions for the LLM.
     */
    getDefinitions(): ToolDefinition[] {
        return Array.from(this.tools.values()).map(t => t.definition);
    }

    /**
     * Check if a tool is registered.
     */
    has(name: string): boolean {
        return this.tools.has(name);
    }

    /**
     * Execute a tool by name with arguments.
     */
    async execute(name: string, args: Record<string, unknown>, context: ToolContext): Promise<string> {
        const tool = this.tools.get(name);
        if (!tool) {
            throw new Error(`Tool "${name}" not found in registry.`);
        }

        try {
            return await tool.execute(args, context);
        } catch (error) {
            const msg = error instanceof Error ? error.message : String(error);
            return `Tool execution failed: ${msg}`;
        }
    }
}
