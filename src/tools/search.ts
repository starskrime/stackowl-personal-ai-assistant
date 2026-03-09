/**
 * StackOwl — Google Search Tool (Crawlee + Puppeteer)
 *
 * Opens a real headless browser (system Chrome when available), performs a
 * Google search, and returns structured results — title, URL, snippet.
 * No API key required.
 */

import type { ToolImplementation, ToolContext } from './registry.js';
import { PuppeteerCrawler } from '@crawlee/puppeteer';

// Keep Crawlee storage out of the project workspace
process.env['CRAWLEE_STORAGE_DIR'] = '/tmp/stackowl-crawlee';

interface SearchResult {
    title: string;
    url: string;
    snippet: string;
}

export const GoogleSearchTool: ToolImplementation = {
    definition: {
        name: 'google_search',
        description: 'Search Google and return the top results (title, URL, snippet). Use this to find current news, research topics, or look up anything on the web.',
        parameters: {
            type: 'object',
            properties: {
                query: {
                    type: 'string',
                    description: 'The search query (e.g. "latest AI news", "TypeScript best practices 2025")',
                },
                num: {
                    type: 'number',
                    description: 'Number of results to return (default 8, max 15)',
                },
            },
            required: ['query'],
        },
    },

    async execute(args: Record<string, unknown>, _context: ToolContext): Promise<string> {
        const query = (args['query'] as string)?.trim();
        if (!query) throw new Error('Search query is required');

        const limit = Math.min(Number(args['num'] ?? 8), 15);
        const results: SearchResult[] = [];

        const searchUrl =
            `https://www.google.com/search?q=${encodeURIComponent(query)}&hl=en&num=${limit}&gl=us`;

        const crawler = new PuppeteerCrawler({
            maxRequestsPerCrawl: 1,
            requestHandlerTimeoutSecs: 25,
            maxRequestRetries: 1,

            launchContext: {
                // Prefer the user's installed arm64 Chrome over bundled Chromium
                useChrome: true,
                launchOptions: {
                    headless: true,
                    args: [
                        '--no-sandbox',
                        '--disable-setuid-sandbox',
                        '--disable-blink-features=AutomationControlled',
                        '--disable-infobars',
                        '--window-size=1280,900',
                        '--disable-dev-shm-usage',
                    ],
                },
            },

            // Inject stealth overrides BEFORE Google loads the page
            preNavigationHooks: [
                async ({ page }) => {
                    await page.setViewport({ width: 1280, height: 900 });

                    await page.evaluateOnNewDocument(() => {
                        // Hide the webdriver flag — the #1 bot signal
                        Object.defineProperty(navigator, 'webdriver', { get: () => false });
                        // Make chrome object present (real Chrome has it)
                        (window as unknown as Record<string, unknown>)['chrome'] = { runtime: {} };
                        // Mock a few plugins so it doesn't look like a bare Chromium
                        Object.defineProperty(navigator, 'plugins', {
                            get: () => Object.assign([], { length: 3 }),
                        });
                        Object.defineProperty(navigator, 'languages', {
                            get: () => ['en-US', 'en'],
                        });
                    });

                    await page.setExtraHTTPHeaders({
                        'Accept-Language': 'en-US,en;q=0.9',
                        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                    });
                },
            ],

            async requestHandler({ page }) {
                // Accept any cookie/consent banner Google might show
                await page.evaluate(() => {
                    const acceptBtn = document.querySelector<HTMLElement>(
                        'button[id*="accept"], button[jsname="higCR"], #L2AGLb, [aria-label*="Accept"]'
                    );
                    acceptBtn?.click();
                }).catch(() => {});

                // Wait for search results container
                await page.waitForSelector('#search, #rso, .g', { timeout: 12000 }).catch(() => {});

                const extracted = await page.evaluate((maxResults: number) => {
                    const items: SearchResult[] = [];

                    // Try multiple Google result container selectors (Google changes these often)
                    const containers = document.querySelectorAll('#rso .g, #search .g, div[data-hveid] h3');

                    const seen = new Set<string>();

                    for (const el of Array.from(containers)) {
                        if (items.length >= maxResults) break;

                        // Handle both .g containers and bare h3s
                        const h3 = el.tagName === 'H3' ? el : el.querySelector('h3');
                        if (!h3) continue;

                        const title = h3.textContent?.trim() ?? '';
                        if (!title) continue;

                        const anchor = (h3 as HTMLElement).closest('a')
                            ?? el.querySelector<HTMLAnchorElement>('a[href]');
                        const href = (anchor as HTMLAnchorElement | null)?.href ?? '';

                        // Skip Google-internal navigation links
                        if (!href || href.startsWith('https://www.google.com/search') || href.startsWith('#')) continue;
                        if (seen.has(href)) continue;
                        seen.add(href);

                        // Snippet — Google uses many different class names
                        const snippetEl = el.querySelector(
                            '[data-snf] span, [data-sncf] span, .VwiC3b, .lEBKkf, .MUxGbd'
                        );
                        const snippet = snippetEl?.textContent?.trim() ?? '';

                        items.push({ title, url: href, snippet });
                    }

                    return items;
                }, limit);

                results.push(...extracted);
            },
        });

        try {
            await crawler.run([searchUrl]);
        } catch (err) {
            throw new Error(`Google search failed: ${err instanceof Error ? err.message : String(err)}`);
        }

        if (results.length === 0) {
            return (
                `No results extracted for "${query}". ` +
                `Google may have shown a CAPTCHA or consent screen. ` +
                `Try using web_crawl on a specific news site URL instead.`
            );
        }

        const lines = results.map((r, i) =>
            `${i + 1}. **${r.title}**\n   ${r.url}\n   ${r.snippet || '(no snippet)'}`,
        );

        return `Google search results for: "${query}"\n\n${lines.join('\n\n')}`;
    },
};
