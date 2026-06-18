import type {
  BoundingBox,
  UIElement,
  Region,
  ScreenGraph as ScreenGraphType,
  Point,
} from "../types.js";
import { ScreenGraphIndex } from "./spatial-index.js";

export interface ScreenGraphConfig {
  maxAge?: number;
  enableDeltaCompression?: boolean;
}

interface Relationship {
  type: "contains" | "siblings" | "overlaps" | "z_order" | "tab_chain";
  targetId: string;
}

export class ScreenGraphBuilder {
  private elements: Map<string, UIElement> = new Map();
  private relationships: Map<string, Relationship[]> = new Map();
  private index: ScreenGraphIndex;
  private resolution: { width: number; height: number } = { width: 1920, height: 1080 };
  private config: Required<ScreenGraphConfig>;

  constructor(config: ScreenGraphConfig = {}) {
    this.config = {
      maxAge: config.maxAge ?? 5000,
      enableDeltaCompression: config.enableDeltaCompression ?? true,
    };
    this.index = new ScreenGraphIndex();
  }

  setResolution(width: number, height: number): this {
    this.resolution = { width, height };
    return this;
  }

  addElement(element: UIElement): this {
    this.elements.set(element.id, element);
    this.index.add(element);
    return this;
  }

  addElements(elements: UIElement[]): this {
    for (const elem of elements) {
      this.addElement(elem);
    }
    return this;
  }

  addRelationship(fromId: string, toId: string, type: Relationship["type"]): this {
    if (!this.relationships.has(fromId)) {
      this.relationships.set(fromId, []);
    }
    this.relationships.get(fromId)!.push({ type, targetId: toId });
    return this;
  }

  computeRelationships(): this {
    this.relationships.clear();

    for (const [id, elem] of this.elements) {
      const rels: Relationship[] = [];

      for (const [otherId, other] of this.elements) {
        if (id === otherId) continue;

        const type = this.classifyRelationship(elem, other);
        if (type) {
          rels.push({ type, targetId: otherId });
        }
      }

      if (rels.length > 0) {
        this.relationships.set(id, rels);
      }
    }

    return this;
  }

  private classifyRelationship(
    a: UIElement,
    b: UIElement
  ): Relationship["type"] | null {
    const aBounds = a.bounds;
    const bBounds = b.bounds;

    if (this.contains(aBounds, bBounds)) {
      return "contains";
    }

    if (this.overlaps(aBounds, bBounds)) {
      return "overlaps";
    }

    if (this.areSiblings(a, b)) {
      return "siblings";
    }

    if (Math.abs(aBounds.y - bBounds.y) < 5 && Math.abs(aBounds.x - bBounds.x) > 50) {
      return "siblings";
    }

    return null;
  }

  private contains(outer: BoundingBox, inner: BoundingBox): boolean {
    return (
      inner.x >= outer.x &&
      inner.y >= outer.y &&
      inner.x + inner.width <= outer.x + outer.width &&
      inner.y + inner.height <= outer.y + outer.height &&
      (inner.width < outer.width || inner.height < outer.height)
    );
  }

  private overlaps(a: BoundingBox, b: BoundingBox): boolean {
    const overlapX = Math.max(0, Math.min(a.x + a.width, b.x + b.width) - Math.max(a.x, b.x));
    const overlapY = Math.max(0, Math.min(a.y + a.height, b.y + b.height) - Math.max(a.y, b.y));
    const overlapArea = overlapX * overlapY;
    const minArea = Math.min(a.width * a.height, b.width * b.height);
    return overlapArea > minArea * 0.3;
  }

  private areSiblings(a: UIElement, b: UIElement): boolean {
    for (const [_id, elem] of this.elements) {
      if (this.contains(elem.bounds, a.bounds) && this.contains(elem.bounds, b.bounds)) {
        return true;
      }
    }
    return false;
  }

  build(focus?: { app: string; element: string | null; cursor: Point }): ScreenGraphType {
    const regions = this.identifyRegions();

    return {
      id: `sg_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
      timestamp: Date.now(),
      resolution: this.resolution,
      elements: new Map(this.elements),
      regions,
      focus: focus || {
        app: "",
        element: null,
        cursor: { x: 0, y: 0 },
      },
    };
  }

  buildDelta(
    previous: ScreenGraphType,
    focus?: { app: string; element: string | null; cursor: Point }
  ): ScreenGraphType {
    const current = this.build(focus);

    if (!this.config.enableDeltaCompression) {
      return current;
    }

    const addedIds: string[] = [];
    const removedIds: string[] = [];
    const unchangedIds: string[] = [];

    for (const id of current.elements.keys()) {
      if (previous.elements.has(id)) {
        unchangedIds.push(id);
      } else {
        addedIds.push(id);
      }
    }

    for (const id of previous.elements.keys()) {
      if (!current.elements.has(id)) {
        removedIds.push(id);
      }
    }

    return current;
  }

  private identifyRegions(): Region[] {
    const regions: Region[] = [];

    const toolbars = this.identifyToolbars();
    regions.push(...toolbars.map((r) => ({ ...r, id: `region_toolbar_${regions.length}` })));

    const sidebars = this.identifySidebars();
    regions.push(...sidebars.map((r) => ({ ...r, id: `region_sidebar_${regions.length}` })));

    const dialogs = this.identifyDialogs();
    regions.push(...dialogs.map((r) => ({ ...r, id: `region_dialog_${regions.length}` })));

    const content = this.identifyContentArea(regions);
    if (content) {
      regions.push({ ...content, id: `region_content_${regions.length}` });
    }

    return regions;
  }

  private identifyToolbars(): Omit<Region, "id">[] {
    const regions: Omit<Region, "id">[] = [];
    const horizontalStrips: BoundingBox[] = [];

    const buttons = Array.from(this.elements.values()).filter(
      (e) => e.semantic.role === "button" || e.type === "button"
    );

    if (buttons.length < 3) return [];

    const byY = new Map<number, UIElement[]>();
    for (const btn of buttons) {
      const yKey = Math.floor(btn.bounds.y / 20) * 20;
      if (!byY.has(yKey)) byY.set(yKey, []);
      byY.get(yKey)!.push(btn);
    }

    for (const [y, elems] of byY) {
      if (elems.length >= 3 && y < this.resolution.height * 0.15) {
        const minX = Math.min(...elems.map((e) => e.bounds.x));
        const maxX = Math.max(...elems.map((e) => e.bounds.x + e.bounds.width));
        horizontalStrips.push({
          x: minX,
          y,
          width: maxX - minX,
          height: 50,
        });
      }
    }

    const merged = this.mergeStrips(horizontalStrips);
    for (const strip of merged) {
      if (strip.width > this.resolution.width * 0.3) {
        regions.push({
          type: "toolbar",
          bounds: strip,
          elements: this.index.searchByRegion(strip),
        });
      }
    }

    return regions;
  }

  private identifySidebars(): Omit<Region, "id">[] {
    const regions: Omit<Region, "id">[] = [];

    const buttons = Array.from(this.elements.values()).filter(
      (e) => e.semantic.role === "button" || e.type === "button"
    );

    if (buttons.length < 3) return [];

    const byX = new Map<number, UIElement[]>();
    for (const btn of buttons) {
      const xKey = Math.floor(btn.bounds.x / 20) * 20;
      if (!byX.has(xKey)) byX.set(xKey, []);
      byX.get(xKey)!.push(btn);
    }

    for (const [x, elems] of byX) {
      if (elems.length >= 3 && x < this.resolution.width * 0.15) {
        const minY = Math.min(...elems.map((e) => e.bounds.y));
        const maxY = Math.max(...elems.map((e) => e.bounds.y + e.bounds.height));
        const strip = {
          x,
          y: minY,
          width: 200,
          height: maxY - minY,
        };

        if (strip.height > this.resolution.height * 0.3) {
          regions.push({
            type: "sidebar",
            bounds: strip,
            elements: this.index.searchByRegion(strip),
          });
        }
      }
    }

    return regions;
  }

  private identifyDialogs(): Omit<Region, "id">[] {
    const regions: Omit<Region, "id">[] = [];

    for (const [id, elem] of this.elements) {
      if (elem.type === "dialog" || elem.semantic.role === "dialog") {
        regions.push({
          type: "dialog",
          bounds: elem.bounds,
          elements: [id],
        });
      }
    }

    const potentialDialogs = Array.from(this.elements.values()).filter((e) => {
      const aspect = e.bounds.width / Math.max(e.bounds.height, 1);
      return (
        e.bounds.width < this.resolution.width * 0.7 &&
        e.bounds.height < this.resolution.height * 0.7 &&
        e.bounds.width > 100 &&
        e.bounds.height > 100 &&
        aspect > 0.3 &&
        aspect < 3
      );
    });

    for (const dialog of potentialDialogs) {
      const centerX = dialog.bounds.x + dialog.bounds.width / 2;
      const centerY = dialog.bounds.y + dialog.bounds.height / 2;

      const screenCenterX = this.resolution.width / 2;
      const screenCenterY = this.resolution.height / 2;

      if (Math.abs(centerX - screenCenterX) < this.resolution.width * 0.2 &&
          Math.abs(centerY - screenCenterY) < this.resolution.height * 0.2) {
        regions.push({
          type: "dialog",
          bounds: dialog.bounds,
          elements: this.index.searchByRegion(dialog.bounds),
        });
      }
    }

    return regions;
  }

  private identifyContentArea(existingRegions: Region[]): Omit<Region, "id"> | null {
    let contentBounds: BoundingBox | null = null;

    const existingBounds = existingRegions.map((r) => r.bounds);

    for (const [, elem] of this.elements) {
      if (elem.type === "text" || elem.type === "unknown") {
        if (!this.isContainedInAny(elem.bounds, existingBounds)) {
          if (!contentBounds) {
            contentBounds = { ...elem.bounds };
          } else {
            contentBounds = this.unionBounds(contentBounds, elem.bounds);
          }
        }
      }
    }

    if (contentBounds && contentBounds.width > 200 && contentBounds.height > 200) {
      return {
        type: "content",
        bounds: contentBounds,
        elements: this.index.searchByRegion(contentBounds),
      };
    }

    return {
      type: "content",
      bounds: {
        x: 0,
        y: 0,
        width: this.resolution.width,
        height: this.resolution.height,
      },
      elements: [],
    };
  }

  private mergeStrips(strips: BoundingBox[]): BoundingBox[] {
    if (strips.length === 0) return [];

    const sorted = [...strips].sort((a, b) => a.y - b.y || a.x - b.x);
    const merged: BoundingBox[] = [];

    for (const strip of sorted) {
      const last = merged[merged.length - 1];
      if (last && Math.abs(last.y - strip.y) < 10 && Math.abs(last.height - strip.height) < 20) {
        last.width = Math.max(last.width + last.x, strip.width + strip.x) - Math.min(last.x, strip.x);
        last.x = Math.min(last.x, strip.x);
        last.height = Math.max(last.y + last.height, strip.y + strip.height) - Math.min(last.y, strip.y);
      } else {
        merged.push({ ...strip });
      }
    }

    return merged;
  }

  private unionBounds(a: BoundingBox, b: BoundingBox): BoundingBox {
    return {
      x: Math.min(a.x, b.x),
      y: Math.min(a.y, b.y),
      width: Math.max(a.x + a.width, b.x + b.width) - Math.min(a.x, b.x),
      height: Math.max(a.y + a.height, b.y + b.height) - Math.min(a.y, b.y),
    };
  }

  private isContainedInAny(bounds: BoundingBox, regionBounds: BoundingBox[]): boolean {
    for (const region of regionBounds) {
      if (
        bounds.x >= region.x &&
        bounds.y >= region.y &&
        bounds.x + bounds.width <= region.x + region.width &&
        bounds.y + bounds.height <= region.y + region.height
      ) {
        return true;
      }
    }
    return false;
  }

  getIndex(): ScreenGraphIndex {
    return this.index;
  }

  clear(): void {
    this.elements.clear();
    this.relationships.clear();
    this.index.clear();
  }
}

export function createScreenGraph(
  elements: UIElement[],
  resolution: { width: number; height: number },
  focus?: { app: string; element: string | null; cursor: Point }
): ScreenGraphType {
  const builder = new ScreenGraphBuilder().setResolution(resolution.width, resolution.height);

  builder.addElements(elements);
  builder.computeRelationships();

  return builder.build(focus);
}
