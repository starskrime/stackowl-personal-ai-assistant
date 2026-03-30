import type { BoundingBox, UIElement } from "../types.js";

interface RBushItem {
  minX: number;
  minY: number;
  maxX: number;
  maxY: number;
  id: string;
}

export class SpatialIndex {
  private items: RBushItem[] = [];
  private dirty = false;
  private sorted: RBushItem[] | null = null;

  insert(bounds: BoundingBox, id: string): void {
    this.items.push({
      minX: bounds.x,
      minY: bounds.y,
      maxX: bounds.x + bounds.width,
      maxY: bounds.y + bounds.height,
      id,
    });
    this.dirty = true;
  }

  search(query: BoundingBox): string[] {
    this.ensureSorted();
    
    const results: string[] = [];
    const minX = query.x;
    const minY = query.y;
    const maxX = query.x + query.width;
    const maxY = query.y + query.height;

    for (const item of this.sorted!) {
      if (item.minX > maxX || item.maxX < minX) continue;
      if (item.minY > maxY || item.maxY < minY) continue;
      results.push(item.id);
    }

    return results;
  }

  searchPoint(x: number, y: number): string[] {
    return this.search({ x, y, width: 1, height: 1 });
  }

  remove(id: string): void {
    const idx = this.items.findIndex((item) => item.id === id);
    if (idx !== -1) {
      this.items.splice(idx, 1);
      this.dirty = true;
    }
  }

  clear(): void {
    this.items = [];
    this.sorted = null;
    this.dirty = false;
  }

  getAll(): string[] {
    return this.items.map((item) => item.id);
  }

  size(): number {
    return this.items.length;
  }

  private ensureSorted(): void {
    if (this.dirty || !this.sorted) {
      this.sorted = [...this.items].sort((a, b) => {
        if (a.minX !== b.minX) return a.minX - b.minX;
        if (a.minY !== b.minY) return a.minY - b.minY;
        return 0;
      });
      this.dirty = false;
    }
  }

  nearestTo(x: number, y: number, maxDistance: number): string | null {
    this.ensureSorted();

    let nearest: string | null = null;
    let nearestDist = maxDistance;

    for (const item of this.sorted!) {
      const dx = Math.max(item.minX - x, 0, x - item.maxX);
      const dy = Math.max(item.minY - y, 0, y - item.maxY);
      const dist = Math.sqrt(dx * dx + dy * dy);

      if (dist < nearestDist) {
        nearestDist = dist;
        nearest = item.id;
      }
    }

    return nearest;
  }

  intersects(bounds: BoundingBox): boolean {
    return this.search(bounds).length > 0;
  }

  containedIn(bounds: BoundingBox): string[] {
    this.ensureSorted();

    const results: string[] = [];
    const minX = bounds.x;
    const minY = bounds.y;
    const maxX = bounds.x + bounds.width;
    const maxY = bounds.y + bounds.height;

    for (const item of this.sorted!) {
      if (item.minX >= minX && item.minY >= minY && item.maxX <= maxX && item.maxY <= maxY) {
        results.push(item.id);
      }
    }

    return results;
  }

  overlapping(bounds: BoundingBox): string[] {
    return this.search(bounds);
  }
}

export class InvertedIndex {
  private index: Map<string, Set<string>> = new Map();
  private documentTerms: Map<string, Set<string>> = new Map();

  addDocument(id: string, text: string): void {
    const terms = this.tokenize(text);
    const termSet = new Set(terms);
    this.documentTerms.set(id, termSet);

    for (const term of termSet) {
      if (!this.index.has(term)) {
        this.index.set(term, new Set());
      }
      this.index.get(term)!.add(id);
    }
  }

  removeDocument(id: string): void {
    const terms = this.documentTerms.get(id);
    if (!terms) return;

    for (const term of terms) {
      this.index.get(term)?.delete(id);
      if (this.index.get(term)?.size === 0) {
        this.index.delete(term);
      }
    }

    this.documentTerms.delete(id);
  }

  search(query: string): string[] {
    const terms = this.tokenize(query);
    if (terms.length === 0) return [];

    const resultSets = terms.map((term) => this.index.get(term) || new Set<string>());

    if (resultSets.length === 1) {
      return Array.from(resultSets[0]);
    }

    const intersection = new Set<string>();
    for (const id of resultSets[0]) {
      if (resultSets.every((set) => set.has(id))) {
        intersection.add(id);
      }
    }

    return Array.from(intersection);
  }

  searchOR(query: string): string[] {
    const terms = this.tokenize(query);
    if (terms.length === 0) return [];

    const union = new Set<string>();
    for (const term of terms) {
      for (const id of this.index.get(term) || []) {
        union.add(id);
      }
    }

    return Array.from(union);
  }

  searchFuzzy(query: string, maxDistance = 2): string[] {
    const terms = this.tokenize(query);
    const results = new Set<string>();

    for (const [term, ids] of this.index) {
      for (const queryTerm of terms) {
        if (this.levenshteinDistance(term, queryTerm) <= maxDistance) {
          for (const id of ids) {
            results.add(id);
          }
        }
      }
    }

    return Array.from(results);
  }

  clear(): void {
    this.index.clear();
    this.documentTerms.clear();
  }

  size(): number {
    return this.documentTerms.size;
  }

  private tokenize(text: string): string[] {
    return text
      .toLowerCase()
      .replace(/[^\w\s]/g, " ")
      .split(/\s+/)
      .filter((term) => term.length > 1);
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
}

export class ScreenGraphIndex {
  readonly spatial: SpatialIndex;
  readonly text: InvertedIndex;
  readonly roleIndex: Map<string, Set<string>>;

  constructor() {
    this.spatial = new SpatialIndex();
    this.text = new InvertedIndex();
    this.roleIndex = new Map();
  }

  add(element: UIElement): void {
    this.spatial.insert(element.bounds, element.id);

    const searchableText = [
      element.semantic.label,
      element.semantic.role,
      element.semantic.description,
      element.visual.textOcr,
    ]
      .filter(Boolean)
      .join(" ");

    if (searchableText) {
      this.text.addDocument(element.id, searchableText);
    }

    if (element.semantic.role) {
      if (!this.roleIndex.has(element.semantic.role)) {
        this.roleIndex.set(element.semantic.role, new Set());
      }
      this.roleIndex.get(element.semantic.role)!.add(element.id);
    }
  }

  remove(elementId: string): void {
    this.spatial.remove(elementId);
    this.text.removeDocument(elementId);

    for (const [role, ids] of this.roleIndex) {
      ids.delete(elementId);
      if (ids.size === 0) {
        this.roleIndex.delete(role);
      }
    }
  }

  clear(): void {
    this.spatial.clear();
    this.text.clear();
    this.roleIndex.clear();
  }

  searchByRegion(bounds: BoundingBox): string[] {
    return this.spatial.search(bounds);
  }

  searchByText(query: string): string[] {
    return this.text.search(query);
  }

  searchByRole(role: string): string[] {
    return Array.from(this.roleIndex.get(role) || []);
  }

  findByPoint(x: number, y: number): string[] {
    return this.spatial.searchPoint(x, y);
  }

  findNearest(x: number, y: number, maxDistance: number): string | null {
    return this.spatial.nearestTo(x, y, maxDistance);
  }
}
