/**
 * StackOwl — File Tools
 *
 * Allows owls to read and write files directly in the workspace.
 */

import { readFile, writeFile } from 'node:fs/promises';
import { resolve, isAbsolute } from 'node:path';
import type { ToolImplementation, ToolContext } from './registry.js';

export const ReadFileTool: ToolImplementation = {
    definition: {
        name: 'read_file',
        description: 'Read the contents of a file',
        parameters: {
            type: 'object',
            properties: {
                path: {
                    type: 'string',
                    description: 'Path to the file to read (relative to workspace or absolute)',
                },
            },
            required: ['path'],
        },
    },

    async execute(args: Record<string, unknown>, context: ToolContext): Promise<string> {
        const filePath = args['path'] as string;
        if (!filePath) throw new Error('Path argument missing');

        const resolved = isAbsolute(filePath) ? filePath : resolve(context.cwd, filePath);

        try {
            const content = await readFile(resolved, 'utf-8');
            // Truncate if massive
            if (content.length > 20000) {
                return `[File is too large to read entirely. First 10000 chars:]\n\n${content.substring(0, 10000)}\n...[truncated]`;
            }
            return content;
        } catch (error: any) {
            return `Failed to read file: ${error.message}`;
        }
    },
};

export const WriteFileTool: ToolImplementation = {
    definition: {
        name: 'write_file',
        description: 'Write string content to a file (creates or overwrites)',
        parameters: {
            type: 'object',
            properties: {
                path: {
                    type: 'string',
                    description: 'Path to the file to write (relative to workspace or absolute)',
                },
                content: {
                    type: 'string',
                    description: 'The string content to write',
                },
            },
            required: ['path', 'content'],
        },
    },

    async execute(args: Record<string, unknown>, context: ToolContext): Promise<string> {
        const filePath = args['path'] as string;
        const content = args['content'] as string;

        if (!filePath) throw new Error('Path argument missing');
        if (content === undefined) throw new Error('Content argument missing');

        const resolved = isAbsolute(filePath) ? filePath : resolve(context.cwd, filePath);

        try {
            await writeFile(resolved, content, 'utf-8');
            return `Successfully wrote to ${filePath}`;
        } catch (error: any) {
            return `Failed to write file: ${error.message}`;
        }
    },
};
