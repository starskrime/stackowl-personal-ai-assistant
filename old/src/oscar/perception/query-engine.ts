import type { BoundingBox, UIElement, ScreenGraph, Region } from "../types.js";
import { ScreenGraphIndex } from "./spatial-index.js";

export interface QueryOptions {
  limit?: number;
  offset?: number;
  fuzzy?: boolean;
  fuzzyDistance?: number;
}

export interface ElementQuery {
  id?: string;
  type?: UIElement["type"];
  label?: string;
  role?: string;
  bounds?: BoundingBox;
  inRegion?: Region["type"];
  clickable?: boolean;
  editable?: boolean;
}

export class QueryEngine {
  private graph: ScreenGraph;
  private index: ScreenGraphIndex;

  constructor(graph: ScreenGraph) {
    this.graph = graph;
    this.index = new ScreenGraphIndex();

    for (const [, elem] of graph.elements) {
      this.index.add(elem);
    }
  }

  find(query: ElementQuery, options: QueryOptions = {}): UIElement[] {
    const { limit = 50, offset = 0 } = options;

    let results = this.executeQuery(query);

    if (options.fuzzy && query.label) {
      const fuzzyResults = this.fuzzySearch(query.label);
      results = this.mergeResults(results, fuzzyResults);
    }

    results = results.slice(offset, offset + limit);

    return results;
  }

  findOne(query: ElementQuery): UIElement | null {
    const results = this.find(query, { limit: 1 });
    return results[0] || null;
  }

  findByLabel(label: string, options: QueryOptions = {}): UIElement[] {
    const normalizedLabel = label.toLowerCase().trim();

    const results = Array.from(this.graph.elements.values()).filter((elem) => {
      const elemLabel = (elem.semantic.label || "").toLowerCase();
      const textOcr = (elem.visual.textOcr || "").toLowerCase();

      if (options.fuzzy) {
        return (
          this.fuzzyMatch(normalizedLabel, elemLabel) ||
          this.fuzzyMatch(normalizedLabel, textOcr)
        );
      }

      return elemLabel.includes(normalizedLabel) || textOcr.includes(normalizedLabel);
    });

    const limit = options.limit || 50;
    return results.slice(0, limit);
  }

  findByRole(role: string, options: QueryOptions = {}): UIElement[] {
    const normalizedRole = role.toLowerCase();

    const results = Array.from(this.graph.elements.values()).filter((elem) => {
      return (elem.semantic.role || "").toLowerCase().includes(normalizedRole);
    });

    const limit = options.limit || 50;
    return results.slice(0, limit);
  }

  findInRegion(regionType: Region["type"], options: QueryOptions = {}): UIElement[] {
    const region = this.graph.regions.find((r) => r.type === regionType);
    if (!region) return [];

    const elementIds = new Set(region.elements);
    let results = Array.from(this.graph.elements.values()).filter((elem) =>
      elementIds.has(elem.id)
    );

    const limit = options.limit || 50;
    return results.slice(0, limit);
  }

  findByBounds(bounds: BoundingBox, options: QueryOptions = {}): UIElement[] {
    const candidateIds = this.index.searchByRegion(bounds);

    let results = candidateIds
      .map((id) => this.graph.elements.get(id))
      .filter((elem): elem is UIElement => elem !== undefined);

    if (options.limit) {
      results = results.slice(0, options.limit);
    }

    return results;
  }

  findAtPoint(x: number, y: number, options: QueryOptions = {}): UIElement[] {
    const candidateIds = this.index.findByPoint(x, y);

    let results = candidateIds
      .map((id) => this.graph.elements.get(id))
      .filter((elem): elem is UIElement => elem !== undefined);

    results.sort((a, b) => {
      const aArea = a.bounds.width * a.bounds.height;
      const bArea = b.bounds.width * b.bounds.height;
      return aArea - bArea;
    });

    if (options.limit) {
      results = results.slice(0, options.limit);
    }

    return results;
  }

  findClickable(options: QueryOptions = {}): UIElement[] {
    const results = Array.from(this.graph.elements.values()).filter(
      (elem) => elem.affordances.clickable
    );

    const limit = options.limit || 50;
    return results.slice(0, limit);
  }

  findEditable(options: QueryOptions = {}): UIElement[] {
    const results = Array.from(this.graph.elements.values()).filter(
      (elem) => elem.affordances.editable
    );

    const limit = options.limit || 50;
    return results.slice(0, limit);
  }

  findByType(type: UIElement["type"], options: QueryOptions = {}): UIElement[] {
    const results = Array.from(this.graph.elements.values()).filter(
      (elem) => elem.type === type
    );

    const limit = options.limit || 50;
    return results.slice(0, limit);
  }

  findWithText(text: string, options: QueryOptions = {}): UIElement[] {
    const normalizedText = text.toLowerCase();

    const results = Array.from(this.graph.elements.values()).filter((elem) => {
      const label = (elem.semantic.label || "").toLowerCase();
      const textOcr = (elem.visual.textOcr || "").toLowerCase();
      const description = (elem.semantic.description || "").toLowerCase();

      return (
        label.includes(normalizedText) ||
        textOcr.includes(normalizedText) ||
        description.includes(normalizedText)
      );
    });

    const limit = options.limit || 50;
    return results.slice(0, limit);
  }

  getRegions(options: QueryOptions = {}): Region[] {
    const limit = options.limit || 20;
    return this.graph.regions.slice(0, limit);
  }

  getRegionAt(x: number, y: number): Region | null {
    for (const region of this.graph.regions) {
      const r = region.bounds;
      if (x >= r.x && x <= r.x + r.width && y >= r.y && y <= r.y + r.height) {
        return region;
      }
    }
    return null;
  }

  count(query?: ElementQuery): number {
    if (!query) {
      return this.graph.elements.size;
    }
    return this.executeQuery(query).length;
  }

  private executeQuery(query: ElementQuery): UIElement[] {
    const results = Array.from(this.graph.elements.values());

    return results.filter((elem) => {
      if (query.id && elem.id !== query.id) return false;

      if (query.type && elem.type !== query.type) return false;

      if (query.label) {
        const normalizedLabel = query.label.toLowerCase();
        const elemLabel = (elem.semantic.label || "").toLowerCase();
        const textOcr = (elem.visual.textOcr || "").toLowerCase();
        if (!elemLabel.includes(normalizedLabel) && !textOcr.includes(normalizedLabel)) {
          return false;
        }
      }

      if (query.role) {
        const normalizedRole = query.role.toLowerCase();
        const elemRole = (elem.semantic.role || "").toLowerCase();
        if (!elemRole.includes(normalizedRole)) return false;
      }

      if (query.bounds) {
        if (!this.boundsOverlap(elem.bounds, query.bounds)) return false;
      }

      if (query.inRegion) {
        const region = this.graph.regions.find((r) => r.type === query.inRegion);
        if (region && !region.elements.includes(elem.id)) return false;
      }

      if (query.clickable !== undefined && elem.affordances.clickable !== query.clickable) {
        return false;
      }

      if (query.editable !== undefined && elem.affordances.editable !== query.editable) {
        return false;
      }

      return true;
    });
  }

  private fuzzySearch(label: string): UIElement[] {
    const results: UIElement[] = [];

    for (const elem of this.graph.elements.values()) {
      const elemLabel = (elem.semantic.label || "").toLowerCase();
      const textOcr = (elem.visual.textOcr || "").toLowerCase();

      if (this.fuzzyMatch(label.toLowerCase(), elemLabel) ||
          this.fuzzyMatch(label.toLowerCase(), textOcr)) {
        results.push(elem);
      }
    }

    return results;
  }

  private fuzzyMatch(query: string, target: string): boolean {
    if (target.includes(query)) return true;

    const queryWords = query.split(/\s+/);
    const targetWords = target.split(/\s+/);

    for (const qWord of queryWords) {
      for (const tWord of targetWords) {
        if (this.levenshteinDistance(qWord, tWord) <= 2) {
          return true;
        }
      }
    }

    return false;
  }

  private levenshteinDistance(a: string, b: string): number {
    if (a.length === 0) return b.length;
    if (b.length === 0) return a.length;

    const matrix: number[][] = [];

    for (let i = 0; i <= b.length; i++) {
      matrix[i] = [i];
    }

    for (let j = 0; j <= a.length; j++) {
      matrix[0][j] = j;
    }

    for (let i = 1; i <= b.length; i++) {
      for (let j = 1; j <= a.length; j++) {
        if (b.charAt(i - 1) === a.charAt(j - 1)) {
          matrix[i][j] = matrix[i - 1][j - 1];
        } else {
          matrix[i][j] = Math.min(
            matrix[i - 1][j - 1] + 1,
            matrix[i][j - 1] + 1,
            matrix[i - 1][j] + 1
          );
        }
      }
    }

    return matrix[b.length][a.length];
  }

  private boundsOverlap(a: BoundingBox, b: BoundingBox): boolean {
    return !(
      a.x + a.width < b.x ||
      b.x + b.width < a.x ||
      a.y + a.height < b.y ||
      b.y + b.height < a.y
    );
  }

  private mergeResults(a: UIElement[], b: UIElement[]): UIElement[] {
    const seen = new Set<string>();
    const merged: UIElement[] = [];

    for (const elem of a) {
      if (!seen.has(elem.id)) {
        seen.add(elem.id);
        merged.push(elem);
      }
    }

    for (const elem of b) {
      if (!seen.has(elem.id)) {
        seen.add(elem.id);
        merged.push(elem);
      }
    }

    return merged;
  }
}

export class QueryBuilder {
  private engine: QueryEngine;
  private currentQuery: ElementQuery = {};
  private options: QueryOptions = {};

  constructor(graph: ScreenGraph) {
    this.engine = new QueryEngine(graph);
  }

  withLabel(label: string): this {
    this.currentQuery.label = label;
    return this;
  }

  withRole(role: string): this {
    this.currentQuery.role = role;
    return this;
  }

  withType(type: UIElement["type"]): this {
    this.currentQuery.type = type;
    return this;
  }

  inRegion(regionType: Region["type"]): this {
    this.currentQuery.inRegion = regionType;
    return this;
  }

  inBounds(bounds: BoundingBox): this {
    this.currentQuery.bounds = bounds;
    return this;
  }

  atPoint(x: number, y: number): UIElement[] {
    return this.engine.findAtPoint(x, y);
  }

  clickable(): this {
    this.currentQuery.clickable = true;
    return this;
  }

  editable(): this {
    this.currentQuery.editable = true;
    return this;
  }

  limit(n: number): this {
    this.options.limit = n;
    return this;
  }

  fuzzy(): this {
    this.options.fuzzy = true;
    return this;
  }

  execute(): UIElement[] {
    return this.engine.find(this.currentQuery, this.options);
  }

  first(): UIElement | null {
    return this.engine.findOne(this.currentQuery);
  }

  count(): number {
    return this.engine.count(this.currentQuery);
  }

  getRegions(): Region[] {
    return this.engine.getRegions();
  }

  getRegionAt(x: number, y: number): Region | null {
    return this.engine.getRegionAt(x, y);
  }
}
