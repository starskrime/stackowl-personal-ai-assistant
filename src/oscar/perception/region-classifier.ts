import type { BoundingBox, Region, UIElement } from "../types.js";
import type { ScreenGraphIndex } from "./spatial-index.js";

export interface ClassificationResult {
  type: Region["type"];
  confidence: number;
  reasoning: string;
}

interface RegionClassificationRule {
  type: Region["type"];
  priority: number;
  candidates: (
    elements: Map<string, UIElement>,
    index: ScreenGraphIndex,
    resolution: { width: number; height: number }
  ) => { bounds: BoundingBox; elements: string[] }[];
}

export class RegionClassifier {
  private rules: RegionClassificationRule[];

  constructor() {
    this.rules = [
      this.createMenuBarRule(),
      this.createDialogRule(),
      this.createToolbarRule(),
      this.createSidebarRule(),
      this.createStatusBarRule(),
      this.createContentRule(),
    ];
  }

  classify(
    elements: Map<string, UIElement>,
    index: ScreenGraphIndex,
    resolution: { width: number; height: number }
  ): Region[] {
    const regions: Region[] = [];
    const assignedElements = new Set<string>();

    const sortedRules = this.rules.slice().sort((a, b) => b.priority - a.priority);

    for (const rule of sortedRules) {
      const candidates = rule.candidates(elements, index, resolution);

      for (const candidate of candidates) {
        const elementIds = index.searchByRegion(candidate.bounds);

        const unassigned = elementIds.filter((id) => !assignedElements.has(id));

        if (unassigned.length > 0 || candidate.elements.length > 0) {
          regions.push({
            id: `region_${rule.type}_${regions.length}`,
            type: rule.type,
            bounds: candidate.bounds,
            elements: [...new Set([...candidate.elements, ...unassigned])],
          });

          for (const id of unassigned) {
            assignedElements.add(id);
          }
        }
      }
    }

    return regions;
  }

  private createMenuBarRule(): RegionClassificationRule {
    return {
      type: "menu",
      priority: 110,
      candidates: (elements, _index, resolution) => {
        const candidates: { bounds: BoundingBox; elements: string[] }[] = [];

        const menuItems = Array.from(elements.values()).filter(
          (e) =>
            e.type === "menu" ||
            e.semantic.role === "AXMenuBar" ||
            e.semantic.role === "AXMenu" ||
            e.semantic.label?.toLowerCase().includes("file") ||
            e.semantic.label?.toLowerCase().includes("edit") ||
            e.semantic.label?.toLowerCase().includes("view")
        );

        if (menuItems.length >= 3) {
          const bounds = this.computeBounds(menuItems.map((e) => e.bounds));
          if (bounds.y < resolution.height * 0.1 && bounds.height < 50) {
            candidates.push({
              bounds,
              elements: menuItems.map((e) => e.id),
            });
          }
        }

        return candidates;
      },
    };
  }

  private createDialogRule(): RegionClassificationRule {
    return {
      type: "dialog",
      priority: 95,
      candidates: (elements, _index, resolution) => {
        const candidates: { bounds: BoundingBox; elements: string[] }[] = [];

        const potentialDialogs = Array.from(elements.values()).filter((e) => {
          if (e.type === "dialog" || e.semantic.role === "dialog") return true;

          const aspect = e.bounds.width / Math.max(e.bounds.height, 1);
          const isCentered =
            Math.abs(e.bounds.x + e.bounds.width / 2 - resolution.width / 2) <
              resolution.width * 0.15 &&
            Math.abs(e.bounds.y + e.bounds.height / 2 - resolution.height / 2) <
              resolution.height * 0.15;

          return (
            e.bounds.width < resolution.width * 0.8 &&
            e.bounds.height < resolution.height * 0.8 &&
            e.bounds.width > 150 &&
            e.bounds.height > 100 &&
            aspect > 0.3 &&
            aspect < 5 &&
            isCentered
          );
        });

        for (const dialog of potentialDialogs) {
          candidates.push({
            bounds: dialog.bounds,
            elements: [dialog.id],
          });
        }

        return candidates;
      },
    };
  }

  private createToolbarRule(): RegionClassificationRule {
    return {
      type: "toolbar",
      priority: 100,
      candidates: (elements, _index, resolution) => {
        const candidates: { bounds: BoundingBox; elements: string[] }[] = [];

        const buttons = Array.from(elements.values()).filter(
          (e) => e.type === "button" || e.semantic.role === "AXButton"
        );

        if (buttons.length < 3) return [];

        const byY = this.groupByPosition(buttons, "y", 20);

        for (const [yKey, group] of byY) {
          if (group.length >= 3 && yKey < resolution.height * 0.2) {
            const bounds = this.computeBounds(group.map((e) => e.bounds));
            if (bounds.width > resolution.width * 0.3) {
              candidates.push({
                bounds,
                elements: group.map((e) => e.id),
              });
            }
          }
        }

        return candidates;
      },
    };
  }

  private createSidebarRule(): RegionClassificationRule {
    return {
      type: "sidebar",
      priority: 90,
      candidates: (elements, _index, resolution) => {
        const candidates: { bounds: BoundingBox; elements: string[] }[] = [];

        const buttons = Array.from(elements.values()).filter(
          (e) => e.type === "button" || e.semantic.role === "AXButton"
        );

        if (buttons.length < 3) return [];

        const byX = this.groupByPosition(buttons, "x", 30);

        for (const [xKey, group] of byX) {
          if (group.length >= 3 && xKey < resolution.width * 0.2) {
            const bounds = this.computeBounds(group.map((e) => e.bounds));
            if (bounds.height > resolution.height * 0.3) {
              candidates.push({
                bounds,
                elements: group.map((e) => e.id),
              });
            }
          }
        }

        return candidates;
      },
    };
  }

  private createStatusBarRule(): RegionClassificationRule {
    return {
      type: "statusbar",
      priority: 50,
      candidates: (elements, _index, resolution) => {
        const candidates: { bounds: BoundingBox; elements: string[] }[] = [];

        const potentialStatus = Array.from(elements.values()).filter((e) => {
          const isBottom = e.bounds.y + e.bounds.height > resolution.height * 0.9;
          const isSmall = e.bounds.height < 30;
          return isBottom && isSmall;
        });

        if (potentialStatus.length > 0) {
          const bounds = this.computeBounds(potentialStatus.map((e) => e.bounds));
          candidates.push({
            bounds,
            elements: potentialStatus.map((e) => e.id),
          });
        }

        return candidates;
      },
    };
  }

  private createContentRule(): RegionClassificationRule {
    return {
      type: "content",
      priority: 10,
      candidates: (_elements, _index, resolution) => {
        return [
          {
            bounds: {
              x: 0,
              y: 0,
              width: resolution.width,
              height: resolution.height,
            },
            elements: [],
          },
        ];
      },
    };
  }

  private groupByPosition(
    elements: UIElement[],
    dimension: "x" | "y",
    tolerance: number
  ): Map<number, UIElement[]> {
    const groups = new Map<number, UIElement[]>();

    for (const elem of elements) {
      const key = Math.floor(elem.bounds[dimension] / tolerance) * tolerance;
      if (!groups.has(key)) {
        groups.set(key, []);
      }
      groups.get(key)!.push(elem);
    }

    return groups;
  }

  private computeBounds(bounds: BoundingBox[]): BoundingBox {
    if (bounds.length === 0) {
      return { x: 0, y: 0, width: 0, height: 0 };
    }

    let minX = Infinity;
    let minY = Infinity;
    let maxX = -Infinity;
    let maxY = -Infinity;

    for (const b of bounds) {
      minX = Math.min(minX, b.x);
      minY = Math.min(minY, b.y);
      maxX = Math.max(maxX, b.x + b.width);
      maxY = Math.max(maxY, b.y + b.height);
    }

    return {
      x: minX,
      y: minY,
      width: maxX - minX,
      height: maxY - minY,
    };
  }

  classifyWithConfidence(
    element: UIElement,
    regions: Region[]
  ): ClassificationResult {
    for (const region of regions) {
      if (this.elementInRegion(element, region)) {
        return {
          type: region.type,
          confidence: 0.9,
          reasoning: `Element is contained in ${region.type} region`,
        };
      }
    }

    return {
      type: "unknown",
      confidence: 0.5,
      reasoning: "Element does not match any known region type",
    };
  }

  private elementInRegion(element: UIElement, region: Region): boolean {
    const r = region.bounds;
    const e = element.bounds;

    return (
      e.x >= r.x &&
      e.y >= r.y &&
      e.x + e.width <= r.x + r.width &&
      e.y + e.height <= r.y + r.height
    );
  }
}

interface VisualPattern {
  id: string;
  regionType: Region["type"];
  elementCount: number;
  density: number;
  layout: "horizontal" | "vertical" | "grid" | "unknown";
  commonRoles: string[];
}

export class VisualPatternLearner {
  private patterns: Map<string, VisualPattern> = new Map();

  learnFromRegion(region: Region, elements: UIElement[]): void {
    const pattern: VisualPattern = {
      id: `pattern_${region.type}_${Date.now()}`,
      regionType: region.type,
      elementCount: elements.length,
      density: this.computeDensity(region.bounds, elements),
      layout: this.analyzeLayout(elements),
      commonRoles: this.extractCommonRoles(elements),
    };

    this.patterns.set(region.type, pattern);
  }

  matchRegion(region: Region, elements: UIElement[]): number {
    const expected = this.patterns.get(region.type);
    if (!expected) return 0;

    const actual: VisualPattern = {
      id: "actual",
      regionType: region.type,
      elementCount: elements.length,
      density: this.computeDensity(region.bounds, elements),
      layout: this.analyzeLayout(elements),
      commonRoles: this.extractCommonRoles(elements),
    };

    return this.computeSimilarity(expected, actual);
  }

  private computeDensity(bounds: BoundingBox, elements: UIElement[]): number {
    const regionArea = bounds.width * bounds.height;
    const elementAreas = elements.reduce(
      (sum, e) => sum + e.bounds.width * e.bounds.height,
      0
    );
    return elementAreas / Math.max(regionArea, 1);
  }

  private analyzeLayout(elements: UIElement[]): "horizontal" | "vertical" | "grid" | "unknown" {
    if (elements.length < 2) return "unknown";

    const positions = elements.map((e) => ({
      x: e.bounds.x + e.bounds.width / 2,
      y: e.bounds.y + e.bounds.height / 2,
    }));

    let horizontalCount = 0;
    let verticalCount = 0;

    for (let i = 0; i < positions.length - 1; i++) {
      for (let j = i + 1; j < positions.length; j++) {
        const dx = Math.abs(positions[i].x - positions[j].x);
        const dy = Math.abs(positions[i].y - positions[j].y);

        if (dx > dy * 2) horizontalCount++;
        if (dy > dx * 2) verticalCount++;
      }
    }

    if (horizontalCount > positions.length * 0.3) return "horizontal";
    if (verticalCount > positions.length * 0.3) return "vertical";
    return "grid";
  }

  private extractCommonRoles(elements: UIElement[]): string[] {
    const roleCounts = new Map<string, number>();

    for (const elem of elements) {
      const role = elem.semantic.role || "unknown";
      roleCounts.set(role, (roleCounts.get(role) || 0) + 1);
    }

    return Array.from(roleCounts.entries())
      .sort((a, b) => b[1] - a[1])
      .slice(0, 3)
      .map(([role]) => role);
  }

  private computeSimilarity(a: VisualPattern, b: VisualPattern): number {
    let score = 0;

    if (a.regionType === b.regionType) score += 0.3;

    const countDiff = Math.abs(a.elementCount - b.elementCount);
    score += Math.max(0, 0.2 - countDiff * 0.02);

    const densityDiff = Math.abs(a.density - b.density);
    score += Math.max(0, 0.2 - densityDiff);

    if (a.layout === b.layout) score += 0.15;

    const commonRolesA = new Set(a.commonRoles);
    const commonRolesB = new Set(b.commonRoles);
    let roleOverlap = 0;
    for (const role of commonRolesA) {
      if (commonRolesB.has(role)) roleOverlap++;
    }
    score += (roleOverlap / Math.max(commonRolesA.size, commonRolesB.size)) * 0.15;

    return Math.min(1, score);
  }
}
