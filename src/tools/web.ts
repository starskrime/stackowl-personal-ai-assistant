/**
 * StackOwl — Web Search Tool
 *
 * Allows owls to fetch basic text from a webpage.
 */

import type { ToolImplementation, ToolContext } from './registry.js';

export const WebFetchTool: ToolImplementation = {
    definition: {
        name: 'fetch_webpage',
        description: 'Fetch the text content of a webpage via HTTP GET.',
        parameters: {
            type: 'object',
            properties: {
                url: {
                    type: 'string',
                    description: 'The full URL to fetch (e.g., https://example.com)',
                },
            },
            required: ['url'],
        },
    },

    async execute(args: Record<string, unknown>, _context: ToolContext): Promise<string> {
        const url = args['url'] as string;
        if (!url) throw new Error('URL argument missing');

        try {
            const response = await fetch(url, {
                headers: {
                    'User-Agent': 'StackOwl Personal AI Assistant (Mozilla/5.0 compatible)',
                },
                signal: AbortSignal.timeout(10000), // 10s timeout
            });

            if (!response.ok) {
                return `HTTP Error: ${response.status} ${response.statusText}`;
            }

            const text = await response.text();

            // Basic HTML stripping to save context space
            let stripped = text
                .replace(/<script\b[^<]*(?:(?!<\/script>)<[^<]*)*<\/script>/gi, '') // Remove scripts
                .replace(/<style\b[^<]*(?:(?!<\/style>)<[^<]*)*<\/style>/gi, '')   // Remove styles
                .replace(/<[^>]+>/g, ' ')                                         // Remove all HTML tags
                .replace(/\s+/g, ' ')                                             // Collapse whitespace
                .trim();

            if (stripped.length > 8000) {
                stripped = stripped.substring(0, 8000) + '... [truncated due to length]';
            }

            return stripped;
        } catch (error: any) {
            return `Failed to fetch URL: ${error.message}`;
        }
    },
};
