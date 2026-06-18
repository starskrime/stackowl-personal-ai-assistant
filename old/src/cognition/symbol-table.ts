import { EventEmitter } from "node:events";
import { log } from "../logger.js";

// ─── Slot definitions ─────────────────────────────────────────────

export type SlotKey =
  | "contextOutput"    // Full ContextPipeline output (the expensive 30-layer build)
  | "preferences"      // Extracted user preferences (text)
  | "namedEntities"    // JSON: {people, projects, places, identifiers, dates}
  | "owlState"         // JSON: {mood, focus, energyLevel, lastUpdatedTurn}
  | "histSummary"      // Compaction output / session history summary
  | "memoryDigest"     // Rolling turn-by-turn memory patch
  | "skillList"        // Available skills (text list)
  | "toolManifest";    // Static tool metadata (rarely changes)

export type InvalidationEvent =
  | "compaction"
  | "learningSession"
  | "preferenceWrite"
  | "dnaMutation"
  | "skillChange"
  | "sessionStart";

const INVALIDATION_MAP: Record<InvalidationEvent, SlotKey[]> = {
  sessionStart:    ["contextOutput", "preferences", "namedEntities", "owlState", "histSummary", "memoryDigest", "skillList"],
  compaction:      ["contextOutput", "histSummary", "memoryDigest"],
  learningSession: ["contextOutput", "memoryDigest", "namedEntities"],
  preferenceWrite: ["preferences", "contextOutput"],
  dnaMutation:     ["owlState"],
  skillChange:     ["skillList", "contextOutput"],
};

// Hard TTL ceilings — stale even without events
const TTL_TURNS: Partial<Record<SlotKey, number>> = {
  owlState: 20,
  memoryDigest: 50,
  contextOutput: 30,
};

const TTL_MINUTES: Partial<Record<SlotKey, number>> = {
  owlState: 15,
  contextOutput: 10,
};

// ─── Slot ────────────────────────────────────────────────────────

export interface SymbolTableSlot {
  content: string;
  version: number;
  lastUpdatedAt: number;
  lastUpdatedTurn: number;
  stale: boolean;
}

// ─── SymbolTable ─────────────────────────────────────────────────

export class SymbolTable extends EventEmitter {
  private readonly slots = new Map<SlotKey, SymbolTableSlot>();
  private _version = 0;
  private _turnIndex = 0;
  readonly sessionId: string;

  constructor(sessionId: string) {
    super();
    this.sessionId = sessionId;
  }

  get version(): number { return this._version; }
  get turnIndex(): number { return this._turnIndex; }

  // ─── Slot access ────────────────────────────────────────────

  get(key: SlotKey): string {
    return this.slots.get(key)?.content ?? "";
  }

  getSlot(key: SlotKey): SymbolTableSlot | undefined {
    return this.slots.get(key);
  }

  has(key: SlotKey): boolean {
    const slot = this.slots.get(key);
    return slot !== undefined && !slot.stale && slot.content.length > 0;
  }

  set(key: SlotKey, content: string): void {
    const prev = this.slots.get(key);
    this._version++;
    this.slots.set(key, {
      content,
      version: this._version,
      lastUpdatedAt: Date.now(),
      lastUpdatedTurn: this._turnIndex,
      stale: false,
    });
    log.cognition.debug("symbol-table.set", {
      sessionId: this.sessionId,
      key,
      version: this._version,
      prevVersion: prev?.version ?? null,
      contentLen: content.length,
    });
  }

  // ─── Turn lifecycle ──────────────────────────────────────────

  onTurnStart(): void {
    this._turnIndex++;
    this.enforceTTLs();
  }

  private enforceTTLs(): void {
    const now = Date.now();
    for (const [key, slot] of this.slots) {
      if (slot.stale) continue;
      const ttlTurns = TTL_TURNS[key];
      if (ttlTurns !== undefined && (this._turnIndex - slot.lastUpdatedTurn) >= ttlTurns) {
        this.markStale(key, "ttl_turns");
        continue;
      }
      const ttlMin = TTL_MINUTES[key];
      if (ttlMin !== undefined && (now - slot.lastUpdatedAt) >= ttlMin * 60_000) {
        this.markStale(key, "ttl_minutes");
      }
    }
  }

  // ─── Invalidation ────────────────────────────────────────────

  invalidate(event: InvalidationEvent): SlotKey[] {
    const affected = INVALIDATION_MAP[event] ?? [];
    for (const key of affected) this.markStale(key, `event:${event}`);
    log.cognition.info("symbol-table.invalidate", { sessionId: this.sessionId, event, affected });
    return affected;
  }

  private markStale(key: SlotKey, reason: string): void {
    const slot = this.slots.get(key);
    if (!slot || slot.stale) return;
    slot.stale = true;
    log.cognition.debug("symbol-table.stale", { sessionId: this.sessionId, key, reason });
    this.emit("stale", key);
  }

  isStale(key: SlotKey): boolean {
    const slot = this.slots.get(key);
    return !slot || slot.stale || slot.content.length === 0;
  }

  // ─── Session warmth score ────────────────────────────────────

  warmth(): number {
    let score = 0;
    if (this.get("preferences").length > 50) score++;
    if (this.get("memoryDigest").length > 100) score++;
    if (this.get("namedEntities").length > 20) score++;
    if (this._turnIndex >= 10) score++;
    return score; // 0-4; threshold ≥2 → use compiled pipeline
  }

  // ─── Summary for Dispatch prompt ────────────────────────────

  summaryForDispatch(): string {
    const lines: string[] = [];
    const prefs = this.get("preferences");
    if (prefs) lines.push(`Preferences: ${prefs.slice(0, 300)}`);
    const entities = this.get("namedEntities");
    if (entities) {
      try {
        const parsed = JSON.parse(entities) as Record<string, string[]>;
        const flat = Object.entries(parsed)
          .filter(([, v]) => v.length > 0)
          .map(([k, v]) => `${k}: ${v.join(", ")}`)
          .join(" | ");
        if (flat) lines.push(`Known entities: ${flat}`);
      } catch { lines.push(`Entities: ${entities.slice(0, 200)}`); }
    }
    const hist = this.get("histSummary");
    if (hist) lines.push(`Session history: ${hist.slice(0, 300)}`);
    const owl = this.get("owlState");
    if (owl) {
      try {
        const state = JSON.parse(owl) as Record<string, unknown>;
        lines.push(`Owl state: mood=${state["mood"] ?? "?"} focus=${state["focus"] ?? "?"}`);
      } catch { /* skip */ }
    }
    lines.push(`Turn: ${this._turnIndex} | Warmth: ${this.warmth()}/4`);
    return lines.join("\n") || "(new session — no context yet)";
  }

  // ─── Debug ──────────────────────────────────────────────────

  staleness(): Partial<Record<SlotKey, boolean>> {
    const out: Partial<Record<SlotKey, boolean>> = {};
    for (const [k, v] of this.slots) out[k] = v.stale;
    return out;
  }
}

// ─── Session registry ────────────────────────────────────────────

export class SymbolTableRegistry {
  private readonly tables = new Map<string, SymbolTable>();

  getOrCreate(sessionId: string): SymbolTable {
    let table = this.tables.get(sessionId);
    if (!table) {
      table = new SymbolTable(sessionId);
      this.tables.set(sessionId, table);
      log.cognition.info("symbol-table.created", { sessionId });
    }
    return table;
  }

  get(sessionId: string): SymbolTable | undefined {
    return this.tables.get(sessionId);
  }

  drop(sessionId: string): void {
    this.tables.delete(sessionId);
    log.cognition.debug("symbol-table.dropped", { sessionId });
  }
}

export const symbolTableRegistry = new SymbolTableRegistry();
