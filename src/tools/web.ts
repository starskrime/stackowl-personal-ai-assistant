/**
 * StackOwl — Web Crawl Tool (Crawlee-powered)
 *
 * Fetches and cleans text from any URL using Crawlee's CheerioCrawler.
 * Uses got-scraping under the hood for real browser-like headers + redirect handling.
 */

import type { ToolImplementation, ToolContext } from './registry.js';
import puppeteer from 'puppeteer';

export const WebCrawlTool: ToolImplementation = {
    definition: {
        name: 'web_crawl',
        description: 'Fetch and read the spatial/semantic content of any webpage. Uses a headless browser to extract the Accessibility Tree, showing you exact buttons, links, and inputs.',
        parameters: {
            type: 'object',
            properties: {
                url: {
                    type: 'string',
                    description: 'Full URL to fetch (e.g. https://example.com/article)',
                }
            },
            required: ['url'],
        },
    },

    async execute(args: Record<string, unknown>, _context: ToolContext): Promise<string> {
        const url = args['url'] as string;
        if (!url?.startsWith('http')) throw new Error('A valid http/https URL is required');

        let browser;
        try {
            browser = await puppeteer.launch({
                headless: true,
                args: ['--no-sandbox', '--disable-setuid-sandbox']
            });
            const page = await browser.newPage();

            // Set a realistic viewport and user agent
            await page.setViewport({ width: 1280, height: 800 });
            await page.setUserAgent('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36');

            // Wait for network idle to ensure SPAs/React apps finish rendering
            await page.goto(url, { waitUntil: 'networkidle2', timeout: 30000 });

            // Extract the Accessibility Tree
            const snapshot = await page.accessibility.snapshot();

            if (!snapshot) {
                return `Failed to extract accessibility tree from ${url}. Page might be completely empty or inaccessible.`;
            }

            const pageTitle = await page.title();

            // Helper to recursively format the AXTree into a readable string
            function formatNode(node: any, depth: number = 0): string {
                let result = '';
                const indent = '  '.repeat(depth);

                // Only include nodes that are semantically meaningful or have text
                const isMeaningful = node.role !== 'generic' && node.role !== 'RootWebArea';
                const hasText = node.name && node.name.trim() !== '';

                if (isMeaningful || hasText) {
                    const role = node.role ? `[${node.role}]` : '';
                    const name = node.name ? ` ${node.name}` : '';
                    const val = node.value ? ` (Value: ${node.value})` : '';
                    const state = node.checked !== undefined ? ` (Checked: ${node.checked})` : '';
                    const disabled = node.disabled ? ` (DISABLED)` : '';

                    if (role || name) {
                        result += `${indent}${role}${name}${val}${state}${disabled}\n`;
                    }
                }

                // Increase depth for children only if this node was meaningful
                const nextDepth = (isMeaningful || hasText) ? depth + 1 : depth;

                if (node.children) {
                    for (const child of node.children) {
                        result += formatNode(child, nextDepth);
                    }
                }

                return result;
            }

            const treeString = formatNode(snapshot);

            const MAX = 20000; // AX Trees are token efficient, we can allow more text
            const truncated = treeString.length > MAX
                ? treeString.slice(0, MAX) + `\n\n... [truncated — ${treeString.length - MAX} chars omitted]`
                : treeString;

            return `### ${pageTitle || url}\n\n${truncated}`;

        } catch (err) {
            throw new Error(`Browser fetch failed: ${err instanceof Error ? err.message : String(err)}`);
        } finally {
            if (browser) await browser.close();
        }
    },
};
