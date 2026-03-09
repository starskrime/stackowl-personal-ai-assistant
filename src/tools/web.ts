/**
 * StackOwl — Web Crawl Tool (Crawlee-powered)
 *
 * Fetches and cleans text from any URL using Crawlee's CheerioCrawler.
 * Uses got-scraping under the hood for real browser-like headers + redirect handling.
 */

import type { ToolImplementation, ToolContext } from './registry.js';
import { CheerioCrawler } from 'crawlee';

// Keep Crawlee storage out of the project workspace
process.env['CRAWLEE_STORAGE_DIR'] = '/tmp/stackowl-crawlee';

export const WebCrawlTool: ToolImplementation = {
    definition: {
        name: 'web_crawl',
        description: 'Fetch and read the text content of any webpage. Use this to read articles, documentation, or any public URL.',
        parameters: {
            type: 'object',
            properties: {
                url: {
                    type: 'string',
                    description: 'Full URL to fetch (e.g. https://example.com/article)',
                },
                selector: {
                    type: 'string',
                    description: 'Optional CSS selector to extract a specific section (e.g. "article", "main", ".content")',
                },
            },
            required: ['url'],
        },
    },

    async execute(args: Record<string, unknown>, _context: ToolContext): Promise<string> {
        const url = args['url'] as string;
        if (!url?.startsWith('http')) throw new Error('A valid http/https URL is required');

        const selector = (args['selector'] as string | undefined)?.trim() || '';
        let pageText = '';
        let pageTitle = '';

        const crawler = new CheerioCrawler({
            maxRequestsPerCrawl: 1,
            requestHandlerTimeoutSecs: 15,
            async requestHandler({ $ }) {
                // Remove noise elements
                $('script, style, nav, header, footer, aside, iframe, noscript, [role="navigation"]').remove();

                if (selector) {
                    const section = $(selector);
                    pageText = section.text();
                } else {
                    // Prefer main content areas
                    const main = $('article, main, [role="main"], .content, #content, .post, .article-body');
                    pageText = main.length > 0 ? main.first().text() : $('body').text();
                }

                pageTitle = $('title').text().trim();
                pageText = pageText.replace(/\s+/g, ' ').trim();
            },
        });

        try {
            await crawler.run([url]);
        } catch (err) {
            throw new Error(`Crawl failed: ${err instanceof Error ? err.message : String(err)}`);
        }

        if (!pageText) return `No readable text found at ${url}`;

        const MAX = 8000;
        const truncated = pageText.length > MAX
            ? pageText.slice(0, MAX) + `\n\n... [truncated — ${pageText.length - MAX} chars omitted]`
            : pageText;

        return `### ${pageTitle || url}\n\n${truncated}`;
    },
};
