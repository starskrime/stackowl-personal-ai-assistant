/**
 * AlwaysOnSignalExtractor — deterministic (zero LLM) extraction of high-stakes
 * signals from every conversation turn. Runs unconditionally; Dispatch cannot
 * gate or skip it.
 */

import { log } from "../logger.js";

// ─── Types ────────────────────────────────────────────────────────

export interface PreferenceSignal {
  raw: string;
  category: "dietary" | "style" | "identity" | "avoid" | "prefer" | "schedule" | "other";
  confidence: number;
}

export interface EntitySignal {
  value: string;
  type: "person" | "project" | "place" | "identifier" | "date";
}

export interface ExtractedSignals {
  preferences: PreferenceSignal[];
  entities: EntitySignal[];
  hasHighStakeSignal: boolean;
}

// ─── Preference patterns ─────────────────────────────────────────

const PREF_PATTERNS: Array<{
  pattern: RegExp;
  category: PreferenceSignal["category"];
  confidence: number;
}> = [
  // Identity assertions
  { pattern: /\bi(?:'m| am) (?:now |a |an )?([\w][\w\s]{2,40})/giu, category: "identity", confidence: 0.9 },
  // Dietary
  { pattern: /\bi(?:'m| am) (?:vegan|vegetarian|kosher|halal|gluten[- ]free|diabetic|lactose[- ]intolerant)\b/giu, category: "dietary", confidence: 0.95 },
  { pattern: /\bno (?:meat|pork|beef|dairy|gluten|shellfish|nuts?|eggs?)\b/giu, category: "dietary", confidence: 0.9 },
  { pattern: /\bi (?:don'?t|cannot|can'?t) eat ([\w\s]{3,30})\b/giu, category: "dietary", confidence: 0.88 },
  // Preferences
  { pattern: /\bi (?:prefer|like|love|enjoy|always use) ([\w][\w\s]{2,40})/giu, category: "prefer", confidence: 0.85 },
  { pattern: /\bi (?:hate|dislike|never use|don'?t (?:like|use|want)) ([\w][\w\s]{2,40})/giu, category: "avoid", confidence: 0.85 },
  // Style directives
  { pattern: /\balways (?:respond|reply|write|format|use) ([\w][\w\s]{2,40})/giu, category: "style", confidence: 0.8 },
  { pattern: /\bnever (?:suggest|recommend|use|send) ([\w][\w\s]{2,40})/giu, category: "avoid", confidence: 0.85 },
  // Schedule
  { pattern: /\bmy (?:timezone|working hours?|schedule) (?:is|are) ([\w\s:+\-/]{3,30})/giu, category: "schedule", confidence: 0.8 },
];

// ─── Entity patterns ─────────────────────────────────────────────

const ENTITY_PATTERNS: Array<{ pattern: RegExp; type: EntitySignal["type"] }> = [
  // Named people
  {
    pattern: /\bmy (?:co[- ]?founder|partner|colleague|manager|boss|team\s*mate|friend|wife|husband|client)\s+(?:is\s+)?([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)/gu,
    type: "person",
  },
  {
    pattern: /\b(?:send|email|message|contact|ask|tell|update|notify|schedule with)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b/gu,
    type: "person",
  },
  // Projects / products
  {
    pattern: /\bmy (?:project|app|product|startup|company|repo|service|tool)\s+(?:is\s+(?:called\s+)?)?["']?([A-Za-z][\w\s\-]{2,30})["']?/giu,
    type: "project",
  },
  {
    pattern: /\bworking on\s+["']?([A-Za-z][\w\s\-]{2,30})["']?/giu,
    type: "project",
  },
  // Dates (ISO and natural)
  {
    pattern: /\b(?:by|before|after|until|on|due)\s+(\d{4}-\d{2}-\d{2}|\w+ \d{1,2}(?:st|nd|rd|th)?(?:,?\s*\d{4})?)\b/giu,
    type: "date",
  },
];

// ─── AlwaysOnSignalExtractor ──────────────────────────────────────

export class AlwaysOnSignalExtractor {
  extract(text: string): ExtractedSignals {
    const start = Date.now();
    const preferences = this.extractPreferences(text);
    const entities = this.extractEntities(text);
    const hasHighStakeSignal = preferences.length > 0 ||
      entities.some((e) => e.type === "person" || e.type === "project");

    if (hasHighStakeSignal) {
      log.cognition.info("signal-extractor.hit", {
        prefCount: preferences.length,
        entityCount: entities.length,
        categories: [...new Set(preferences.map((p) => p.category))],
        durationMs: Date.now() - start,
      });
    } else {
      log.cognition.debug("signal-extractor.no-signals", { durationMs: Date.now() - start });
    }

    return { preferences, entities, hasHighStakeSignal };
  }

  private extractPreferences(text: string): PreferenceSignal[] {
    const found: PreferenceSignal[] = [];
    const seen = new Set<string>();
    for (const { pattern, category, confidence } of PREF_PATTERNS) {
      pattern.lastIndex = 0;
      let m: RegExpExecArray | null;
      while ((m = pattern.exec(text)) !== null) {
        const raw = m[0].trim();
        if (!seen.has(raw)) {
          seen.add(raw);
          found.push({ raw, category, confidence });
        }
      }
    }
    return found;
  }

  private extractEntities(text: string): EntitySignal[] {
    const found: EntitySignal[] = [];
    const seen = new Set<string>();
    for (const { pattern, type } of ENTITY_PATTERNS) {
      pattern.lastIndex = 0;
      let m: RegExpExecArray | null;
      while ((m = pattern.exec(text)) !== null) {
        const value = (m[1] ?? m[0]).trim();
        const key = `${type}:${value.toLowerCase()}`;
        if (!seen.has(key) && value.length >= 2) {
          seen.add(key);
          found.push({ value, type });
        }
      }
    }
    return found;
  }

  /**
   * Merge new EntitySignals into an existing namedEntities JSON slot.
   * Returns the updated JSON string.
   */
  mergeEntitiesIntoSlot(existingJson: string, newEntities: EntitySignal[]): string {
    let existing: Record<string, string[]> = {
      people: [], projects: [], places: [], identifiers: [], dates: [],
    };
    try {
      if (existingJson) existing = JSON.parse(existingJson) as typeof existing;
    } catch { /* start fresh */ }

    for (const entity of newEntities) {
      const key =
        entity.type === "person" ? "people" :
        entity.type === "project" ? "projects" :
        entity.type === "place" ? "places" :
        entity.type === "date" ? "dates" : "identifiers";
      if (!Array.isArray(existing[key])) existing[key] = [];
      if (!existing[key].includes(entity.value)) {
        existing[key].push(entity.value);
      }
    }

    return JSON.stringify(existing);
  }

  /**
   * Merge new PreferenceSignals into a preferences text slot.
   * Each preference occupies one line: "category: raw".
   */
  mergePreferencesIntoSlot(existingText: string, newPrefs: PreferenceSignal[]): string {
    const lines = existingText ? existingText.split("\n").filter(Boolean) : [];
    const lineSet = new Set(lines);
    for (const pref of newPrefs) {
      const line = `${pref.category}: ${pref.raw}`;
      if (!lineSet.has(line)) {
        lineSet.add(line);
        lines.push(line);
      }
    }
    return lines.join("\n");
  }
}

export const alwaysOnExtractor = new AlwaysOnSignalExtractor();
