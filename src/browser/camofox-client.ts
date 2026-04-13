/**
 * StackOwl — CamoFox REST API Client
 *
 * Thin HTTP wrapper around the CamoFox browser server.
 * Used by CamoFoxTool (interactive browsing) and smart-fetch (Tier 4 escalation).
 *
 * CamoFox: https://github.com/jo-inc/camofox-browser
 * Engine: Camoufox — Firefox fork with C++-level fingerprint spoofing.
 */

// ─── Config ──────────────────────────────────────────────────────

export interface CamoFoxClientConfig {
  baseUrl: string;
  apiKey?: string | null;
  defaultUserId?: string;
  defaultTimeout?: number;
}

// ─── Response types ───────────────────────────────────────────────

export interface TabCreateResponse {
  tabId: string;
  snapshot: string;
  refs: Record<string, { role: string; name: string }>;
  url: string;
}

export interface SnapshotResponse {
  snapshot: string;
  refs: Record<string, { role: string; name: string }>;
  url: string;
}

export interface YoutubeTranscriptResponse {
  transcript: string;
  title?: string;
  duration?: number;
}

// ─── Client ──────────────────────────────────────────────────────

export class CamoFoxClient {
  readonly baseUrl: string;
  private readonly headers: Record<string, string>;
  private readonly timeout: number;

  constructor(config: CamoFoxClientConfig) {
    this.baseUrl = config.baseUrl.replace(/\/$/, "");
    this.timeout = config.defaultTimeout ?? 30000;
    this.headers = {
      "Content-Type": "application/json",
      ...(config.apiKey ? { Authorization: `Bearer ${config.apiKey}` } : {}),
    };
  }

  // ─── Internal ────────────────────────────────────────────────

  private async req<T>(
    method: string,
    path: string,
    body?: Record<string, unknown>,
  ): Promise<T> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeout);

    try {
      const res = await fetch(`${this.baseUrl}${path}`, {
        method,
        headers: this.headers,
        body: body ? JSON.stringify(body) : undefined,
        signal: controller.signal,
      });
      clearTimeout(timer);

      if (!res.ok) {
        const err = await res
          .json()
          .catch(() => ({ error: res.statusText })) as { error?: string };
        throw new Error(
          `CamoFox ${method} ${path} → ${res.status}: ${err.error ?? res.statusText}`,
        );
      }

      return res.json() as Promise<T>;
    } catch (err) {
      clearTimeout(timer);
      if (err instanceof Error && err.name === "AbortError") {
        throw new Error(`CamoFox request timed out (${this.timeout}ms): ${method} ${path}`);
      }
      throw err;
    }
  }

  // ─── Session ─────────────────────────────────────────────────

  /** Create a new tab. Optionally navigate to `url` immediately. */
  async createTab(userId: string, url?: string): Promise<TabCreateResponse> {
    return this.req<TabCreateResponse>("POST", "/tabs", {
      userId,
      ...(url ? { url } : {}),
    });
  }

  /** Close a single tab. */
  async closeTab(tabId: string, userId: string): Promise<void> {
    const params = new URLSearchParams({ userId });
    await this.req<unknown>("DELETE", `/tabs/${tabId}?${params}`);
  }

  /** Close all tabs for a user. */
  async closeSession(userId: string): Promise<void> {
    await this.req<unknown>("DELETE", `/sessions/${userId}`);
  }

  // ─── Navigation ──────────────────────────────────────────────

  /**
   * Navigate to a URL or execute a search macro.
   *
   * Macro syntax accepted in `url`:
   *   "@google_search best coffee shops"
   *   "@youtube_search lo-fi beats"
   *
   * Splits on first space and sends as `{ macro, query }`.
   */
  async navigate(
    tabId: string,
    userId: string,
    url: string,
  ): Promise<SnapshotResponse> {
    const macroMatch = url.match(/^(@\w+)\s+(.+)$/s);
    if (macroMatch) {
      return this.req<SnapshotResponse>("POST", `/tabs/${tabId}/navigate`, {
        userId,
        macro: macroMatch[1],
        query: macroMatch[2].trim(),
      });
    }
    return this.req<SnapshotResponse>("POST", `/tabs/${tabId}/navigate`, {
      userId,
      url,
    });
  }

  // ─── Interaction ─────────────────────────────────────────────

  /** Get current page accessibility snapshot. */
  async snapshot(tabId: string, userId: string): Promise<SnapshotResponse> {
    const params = new URLSearchParams({ userId });
    return this.req<SnapshotResponse>("GET", `/tabs/${tabId}/snapshot?${params}`);
  }

  /** Click an element by its `eN` accessibility reference. */
  async click(
    tabId: string,
    userId: string,
    ref: string,
  ): Promise<SnapshotResponse> {
    return this.req<SnapshotResponse>("POST", `/tabs/${tabId}/click`, {
      userId,
      ref,
    });
  }

  /** Type text into an element by its `eN` reference. */
  async type(
    tabId: string,
    userId: string,
    ref: string,
    text: string,
    pressEnter?: boolean,
  ): Promise<SnapshotResponse> {
    return this.req<SnapshotResponse>("POST", `/tabs/${tabId}/type`, {
      userId,
      ref,
      text,
      ...(pressEnter !== undefined ? { pressEnter } : {}),
    });
  }

  /** Scroll the page. */
  async scroll(
    tabId: string,
    userId: string,
    direction: "up" | "down" | "left" | "right",
    amount?: number,
  ): Promise<SnapshotResponse> {
    return this.req<SnapshotResponse>("POST", `/tabs/${tabId}/scroll`, {
      userId,
      direction,
      ...(amount !== undefined ? { amount } : {}),
    });
  }

  /** Wait for a CSS selector or a fixed timeout. */
  async wait(
    tabId: string,
    userId: string,
    selector?: string,
    timeout?: number,
  ): Promise<SnapshotResponse> {
    return this.req<SnapshotResponse>("POST", `/tabs/${tabId}/wait`, {
      userId,
      ...(selector ? { selector } : {}),
      ...(timeout !== undefined ? { timeout } : {}),
    });
  }

  /** Take a screenshot. Returns base64-encoded PNG. */
  async screenshot(tabId: string, userId: string): Promise<string> {
    const params = new URLSearchParams({ userId });
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeout);

    try {
      const res = await fetch(
        `${this.baseUrl}/tabs/${tabId}/screenshot?${params}`,
        { headers: this.headers, signal: controller.signal },
      );
      clearTimeout(timer);

      if (!res.ok) {
        throw new Error(`Screenshot failed: HTTP ${res.status}`);
      }

      const buf = await res.arrayBuffer();
      return Buffer.from(buf).toString("base64");
    } catch (err) {
      clearTimeout(timer);
      if (err instanceof Error && err.name === "AbortError") {
        throw new Error("Screenshot timed out");
      }
      throw err;
    }
  }

  // ─── YouTube ─────────────────────────────────────────────────

  /** Extract YouTube transcript via yt-dlp. */
  async youtubeTranscript(url: string): Promise<YoutubeTranscriptResponse> {
    return this.req<YoutubeTranscriptResponse>("POST", "/youtube/transcript", {
      url,
    });
  }

  // ─── Health ──────────────────────────────────────────────────

  /** Quick check: is the CamoFox server running? */
  async isHealthy(): Promise<boolean> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 3000);
    try {
      const res = await fetch(`${this.baseUrl}/tabs`, {
        headers: this.headers,
        signal: controller.signal,
      });
      clearTimeout(timer);
      return res.status < 500;
    } catch {
      clearTimeout(timer);
      return false;
    }
  }
}

// ─── Module-level singleton (set via initCamoFoxClient) ──────────

let _client: CamoFoxClient | null = null;

export function initCamoFoxClient(config: CamoFoxClientConfig): void {
  _client = new CamoFoxClient(config);
}

export function getCamoFoxClient(): CamoFoxClient | null {
  return _client;
}
