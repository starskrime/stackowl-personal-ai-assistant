export interface SearchResult {
  title: string;
  url: string;
  snippet?: string;
}

/**
 * Parse a Google SERP HTML string into SearchResult[].
 * Three strategies in ranked order:
 *   1. JSON-LD structured data (most stable, crawler-preserved)
 *   2. div.g h3 a[href] (classic organic result container)
 *   3. [data-hveid] h3 a[href] (position-tracking attribute fallback)
 * Returns [] if all strategies yield zero results — never throws.
 */
export function parseGoogleHtml(html: string, _query: string): SearchResult[] {
  // Strategy 1: JSON-LD
  const ldResults = parseJsonLd(html);
  if (ldResults.length > 0) return ldResults;

  // Strategy 2: div.g h3 a
  const divGResults = parseDivG(html);
  if (divGResults.length > 0) return divGResults;

  // Strategy 3: [data-hveid] h3 a
  return parseHveid(html);
}

function parseJsonLd(html: string): SearchResult[] {
  const scriptRe = /<script[^>]+type="application\/ld\+json"[^>]*>([\s\S]*?)<\/script>/gi;
  let m: RegExpExecArray | null;
  while ((m = scriptRe.exec(html)) !== null) {
    try {
      const obj = JSON.parse(m[1]) as Record<string, unknown>;
      const types: unknown[] = Array.isArray(obj["@type"]) ? (obj["@type"] as unknown[]) : [obj["@type"]];
      const isSerp = types.some(t => t === "SearchResultsPage" || t === "ItemList");
      if (!isSerp) continue;

      const rawItems: unknown[] =
        (obj["mainEntity"] as Record<string, unknown>)?.["itemListElement"] as unknown[] ??
        (obj["itemListElement"] as unknown[]) ??
        [];

      const results: SearchResult[] = [];
      for (const item of rawItems) {
        if (!item || typeof item !== "object") continue;
        const i = item as Record<string, unknown>;
        const title = typeof i["name"] === "string" ? i["name"] : typeof i["headline"] === "string" ? i["headline"] : "";
        const url = typeof i["url"] === "string" ? i["url"] : "";
        const snippet = typeof i["description"] === "string" ? i["description"] : undefined;
        if (title && url && url.startsWith("http")) {
          results.push({ title, url, snippet });
        }
      }
      if (results.length > 0) return results;
    } catch {
      // malformed JSON-LD — try next block
    }
  }
  return [];
}

function parseDivG(html: string): SearchResult[] {
  const divGRe = /<div[^>]+class="[^"]*\bg\b[^"]*"[^>]*>[\s\S]*?<h3[^>]*>[\s\S]*?<a[^>]+href="([^"#][^"]*)"[^>]*>([\s\S]*?)<\/a>/gi;
  return extractFromRegex(divGRe, html);
}

function parseHveid(html: string): SearchResult[] {
  const hveidRe = /<[^>]+data-hveid[^>]*>[\s\S]*?<h3[^>]*>[\s\S]*?<a[^>]+href="([^"#][^"]*)"[^>]*>([\s\S]*?)<\/a>/gi;
  return extractFromRegex(hveidRe, html);
}

function extractFromRegex(re: RegExp, html: string): SearchResult[] {
  const results: SearchResult[] = [];
  const seen = new Set<string>();
  let m: RegExpExecArray | null;
  while ((m = re.exec(html)) !== null) {
    const rawUrl = m[1];
    const rawTitle = m[2];
    let url: string;
    try {
      url = decodeURIComponent(rawUrl);
    } catch {
      url = rawUrl;
    }
    const title = rawTitle.replace(/<[^>]+>/g, "").trim();
    if (!url.startsWith("http") || !title || seen.has(url)) continue;
    // Filter out Google's own navigation links
    if (url.includes("google.com/search") || url.includes("google.com/preferences")) continue;
    seen.add(url);
    results.push({ title, url });
  }
  return results;
}
