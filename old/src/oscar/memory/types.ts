export interface BoundingBox {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface VisualSignature {
  iconHash?: string;
  colorHistogram: number[];
  shapeFeatures: {
    aspectRatio: number;
    borderRadius: number;
    hasIcon: boolean;
    iconPosition: "left" | "center" | "right";
  };
  textFeatures: {
    hasText: boolean;
    textLength: number;
    isUppercase: boolean;
    hasShortcut: boolean;
  };
  positionFeatures: {
    region: "toolbar" | "sidebar" | "content" | "dialog" | "menu" | "statusbar" | "unknown";
    alignment: "left" | "center" | "right";
    relativeY: number;
  };
}

export interface Affordance {
  id: string;
  visualSignature: VisualSignature;
  action: string;
  targetRole: string;
  targetLabel?: string;
  app: string;
  successRate: number;
  attempts: number;
  lastAttempt?: number;
  alternatives: string[];
  createdAt: number;
  updatedAt: number;
}

export interface CanonicalAction {
  type: string;
  target: {
    appBundleId?: string;
    windowTitle?: string;
    accessibilityPath?: string;
    visualRegion?: BoundingBox;
    semanticSelector?: {
      role?: string;
      label?: string;
      index?: number;
    };
  };
  params: Record<string, unknown>;
  timestamp: number;
  traceId: string;
}

export interface UIElement {
  id: string;
  type: "button" | "input" | "menu" | "panel" | "toolbar" | "dialog" | "icon" | "text" | "unknown";
  bounds: BoundingBox;
  visual: {
    iconHash?: string;
    textOcr?: string;
    style?: {
      bgColor: string;
      textColor: string;
      fontSize?: number;
    };
  };
  semantic: {
    label?: string;
    role?: string;
    state?: Record<string, boolean>;
    description?: string;
    keyboardShortcut?: string;
  };
  affordances: {
    clickable: boolean;
    editable: boolean;
    scrollable: boolean;
    draggable: boolean;
    keyboardFocusable: boolean;
  };
}

export interface Episode {
  id: string;
  timestamp: number;
  app: string;
  appBundleId?: string;
  actions: CanonicalAction[];
  outcome: "success" | "partial" | "failed";
  userFeedback?: "accepted" | "corrected" | "rejected";
  screenBefore?: string;
  screenAfter?: string;
  error?: string;
  duration?: number;
}

export interface SkillParameter {
  name: string;
  type: "string" | "number" | "boolean" | "path" | "selection";
  description?: string;
  required: boolean;
  defaultValue?: unknown;
}

export interface SkillStep {
  id: string;
  action: string;
  target?: {
    type: "parameter" | "flexible" | "exact";
    paramName?: string;
    role?: string;
    labelPattern?: string;
    label?: string;
    bounds?: BoundingBox;
  };
  parameters?: Record<string, unknown>;
  verification?: {
    type: string;
    expected?: Record<string, unknown>;
    timeout?: number;
  };
}

export interface Skill {
  id: string;
  name: string;
  description: string;
  parameters: SkillParameter[];
  steps: SkillStep[];
  prerequisites?: string[];
  successConditions: {
    type: string;
    expected?: Record<string, unknown>;
  }[];
  sourceApp?: string;
  targetApps: string[];
  successRate: number;
  usageCount: number;
  lastUsed?: number;
  createdAt: number;
  updatedAt: number;
}

export interface MemoryQuery {
  app?: string;
  action?: string;
  role?: string;
  label?: string;
  minSuccessRate?: number;
  since?: number;
  limit?: number;
}

export interface TransferCandidate {
  affordance: Affordance;
  transferabilityScore: number;
  sourceApp: string;
  targetApp: string;
  matchedFeatures: string[];
}

export interface AppCategory {
  name: string;
  apps: string[];
  commonAffordances: string[];
}

export class VisualSignatureExtractor {
  extract(element: UIElement): VisualSignature {
    const bounds = element.bounds;
    const aspectRatio = bounds.width / Math.max(bounds.height, 1);

    return {
      iconHash: element.visual.iconHash,
      colorHistogram: this.extractColorHistogram(element),
      shapeFeatures: {
        aspectRatio,
        borderRadius: this.estimateBorderRadius(element),
        hasIcon: !!element.visual.iconHash,
        iconPosition: "left",
      },
      textFeatures: {
        hasText: !!element.visual.textOcr,
        textLength: element.visual.textOcr?.length || 0,
        isUppercase: this.isUppercase(element),
        hasShortcut: !!element.semantic.keyboardShortcut,
      },
      positionFeatures: {
        region: this.classifyRegion(bounds),
        alignment: this.determineAlignment(bounds),
        relativeY: bounds.y / Math.max(1080, 1),
      },
    };
  }

  private extractColorHistogram(element: UIElement): number[] {
    const style = element.visual.style;
    if (!style) return [];

    const bg = this.parseColor(style.bgColor);
    const fg = this.parseColor(style.textColor);

    return [...bg, ...fg];
  }

  private parseColor(color: string): number[] {
    const match = color.match(/(\d+)/g);
    if (!match) return [0, 0, 0];
    return match.slice(0, 3).map(Number);
  }

  private estimateBorderRadius(element: UIElement): number {
    if (element.type === "button") return 4;
    if (element.type === "input") return 2;
    return 0;
  }

  private isUppercase(element: UIElement): boolean {
    const text = element.semantic.label || element.visual.textOcr || "";
    return text === text.toUpperCase() && text.length > 1;
  }

  private classifyRegion(bounds: BoundingBox): VisualSignature["positionFeatures"]["region"] {
    const relX = bounds.x / 1920;
    const relY = bounds.y / 1080;
    const aspectRatio = bounds.width / bounds.height;

    if (relY < 0.05) return "toolbar";
    if (relY > 0.95) return "statusbar";
    if (relX < 0.1 && aspectRatio < 0.3) return "sidebar";
    if (relX > 0.9 || bounds.width < 300) return "content";
    if (bounds.width < 500 && bounds.height < 400) return "dialog";

    return "content";
  }

  private determineAlignment(bounds: BoundingBox): "left" | "center" | "right" {
    const relX = bounds.x / 1920;
    if (relX < 0.3) return "left";
    if (relX > 0.7) return "right";
    return "center";
  }

  distance(a: VisualSignature, b: VisualSignature): number {
    let score = 0;
    let weights = 0;

    if (a.iconHash && b.iconHash) {
      score += this.iconHashDistance(a.iconHash, b.iconHash);
      weights += 0.3;
    }

    score += this.histogramDistance(a.colorHistogram, b.colorHistogram) * 0.2;
    weights += 0.2;

    score += this.shapeDistance(a.shapeFeatures, b.shapeFeatures) * 0.3;
    weights += 0.3;

    score += this.positionDistance(a.positionFeatures, b.positionFeatures) * 0.2;
    weights += 0.2;

    return weights > 0 ? score / weights : 1;
  }

  private iconHashDistance(a: string, b: string): number {
    if (a === b) return 0;
    const len = Math.max(a.length, b.length);
    if (len === 0) return 1;
    let diff = 0;
    for (let i = 0; i < Math.min(a.length, b.length); i++) {
      if (a[i] !== b[i]) diff++;
    }
    return Math.min(1, diff / len);
  }

  private histogramDistance(a: number[], b: number[]): number {
    if (a.length !== b.length) return 1;
    if (a.length === 0) return 0;
    let sum = 0;
    for (let i = 0; i < a.length; i++) {
      sum += Math.abs(a[i] - b[i]) / 255;
    }
    return sum / a.length;
  }

  private shapeDistance(
    a: VisualSignature["shapeFeatures"],
    b: VisualSignature["shapeFeatures"]
  ): number {
    const aspectDiff = Math.abs(a.aspectRatio - b.aspectRatio) / Math.max(a.aspectRatio, b.aspectRatio, 1);
    const radiusDiff = Math.abs(a.borderRadius - b.borderRadius) / Math.max(a.borderRadius, b.borderRadius, 10);
    const iconDiff = a.hasIcon === b.hasIcon ? 0 : 1;
    const iconPosDiff = a.iconPosition === b.iconPosition ? 0 : 1;
    return (aspectDiff + radiusDiff + iconDiff + iconPosDiff) / 4;
  }

  private positionDistance(
    a: VisualSignature["positionFeatures"],
    b: VisualSignature["positionFeatures"]
  ): number {
    const regionDiff = a.region === b.region ? 0 : 1;
    const alignDiff = a.alignment === b.alignment ? 0 : 1;
    const yDiff = Math.abs(a.relativeY - b.relativeY);
    return (regionDiff + alignDiff + yDiff) / 3;
  }
}

export const visualSignatureExtractor = new VisualSignatureExtractor();
