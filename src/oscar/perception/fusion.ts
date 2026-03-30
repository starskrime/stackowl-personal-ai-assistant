import type { BoundingBox, UIElement, AccessibilityElement } from "../types.js";

export interface OCRResult {
  text: string;
  bounds: BoundingBox;
  confidence: number;
}

export interface YOLOResult {
  class: string;
  confidence: number;
  bounds: BoundingBox;
}

export interface FusionConfig {
  overlapThreshold?: number;
  confidenceWeights?: {
    accessibility?: number;
    ocr?: number;
    yolo?: number;
  };
}

export class PerceptionFusion {
  private config: Required<FusionConfig>;

  constructor(config: FusionConfig = {}) {
    this.config = {
      overlapThreshold: config.overlapThreshold ?? 0.5,
      confidenceWeights: {
        accessibility: config.confidenceWeights?.accessibility ?? 0.9,
        ocr: config.confidenceWeights?.ocr ?? 0.7,
        yolo: config.confidenceWeights?.yolo ?? 0.8,
      },
    };
  }

  fuse(
    accessibilityElements: AccessibilityElement[],
    ocrResults: OCRResult[],
    yoloResults: YOLOResult[]
  ): UIElement[] {
    const elements: UIElement[] = [];
    const usedOcr = new Set<number>();
    const usedYolo = new Set<number>();

    for (const axElem of accessibilityElements) {
      const matchedOcr = this.findMatchingOcr(axElem.bounds, ocrResults, usedOcr);
      const matchedYolo = this.findMatchingYolo(axElem.bounds, yoloResults, usedYolo);

      const element = this.createElement(axElem, matchedOcr, matchedYolo);
      elements.push(element);

      if (matchedOcr) usedOcr.add(ocrResults.indexOf(matchedOcr));
      if (matchedYolo) usedYolo.add(yoloResults.indexOf(matchedYolo));
    }

    for (let i = 0; i < ocrResults.length; i++) {
      if (!usedOcr.has(i)) {
        const ocr = ocrResults[i];
        if (ocr.text.trim().length > 0) {
          elements.push({
            id: `ocr_${i}_${Date.now()}`,
            type: this.inferTypeFromText(ocr.text),
            bounds: ocr.bounds,
            visual: {
              textOcr: ocr.text,
            },
            semantic: {
              label: ocr.text,
            },
            affordances: {
              clickable: false,
              editable: false,
              scrollable: false,
              draggable: false,
              keyboardFocusable: false,
            },
          });
        }
      }
    }

    for (let i = 0; i < yoloResults.length; i++) {
      if (!usedYolo.has(i)) {
        const yolo = yoloResults[i];
        elements.push({
          id: `yolo_${i}_${Date.now()}`,
          type: this.mapClassToType(yolo.class),
          bounds: yolo.bounds,
          visual: {},
          semantic: {
            role: yolo.class,
          },
          affordances: {
            clickable: yolo.class.toLowerCase().includes("button"),
            editable: false,
            scrollable: false,
            draggable: false,
            keyboardFocusable: false,
          },
        });
      }
    }

    return this.mergeOverlappingElements(elements);
  }

  private findMatchingOcr(
    bounds: BoundingBox,
    ocrResults: OCRResult[],
    used: Set<number>
  ): OCRResult | null {
    let bestMatch: OCRResult | null = null;
    let bestOverlap = 0;

    for (let i = 0; i < ocrResults.length; i++) {
      if (used.has(i)) continue;

      const overlap = this.computeOverlap(bounds, ocrResults[i].bounds);
      if (overlap > this.config.overlapThreshold && overlap > bestOverlap) {
        bestMatch = ocrResults[i];
        bestOverlap = overlap;
      }
    }

    return bestMatch;
  }

  private findMatchingYolo(
    bounds: BoundingBox,
    yoloResults: YOLOResult[],
    used: Set<number>
  ): YOLOResult | null {
    let bestMatch: YOLOResult | null = null;
    let bestOverlap = 0;

    for (let i = 0; i < yoloResults.length; i++) {
      if (used.has(i)) continue;

      const overlap = this.computeOverlap(bounds, yoloResults[i].bounds);
      if (overlap > this.config.overlapThreshold && overlap > bestOverlap) {
        bestMatch = yoloResults[i];
        bestOverlap = overlap;
      }
    }

    return bestMatch;
  }

  private createElement(
    axElem: AccessibilityElement,
    ocr: OCRResult | null,
    yolo: YOLOResult | null
  ): UIElement {
    const type = this.mapRoleToType(axElem.role);

    this.computeConfidence(axElem, ocr, yolo);

    const visual: UIElement["visual"] = {};

    if (ocr) {
      visual.textOcr = ocr.text;
    }

    if (yolo) {
      visual.style = {
        bgColor: "#FFFFFF",
        textColor: "#000000",
      };
    }

    const semantic: UIElement["semantic"] = {
      role: axElem.role,
      label: axElem.label,
      description: axElem.description,
      state: axElem.state,
    };

    if (ocr && !semantic.label) {
      semantic.label = ocr.text;
    }

    const affordances = this.inferAffordances(type, axElem);

    return {
      id: `ax_${axElem.id || Date.now()}_${Math.random().toString(36).slice(2, 6)}`,
      type,
      bounds: axElem.bounds,
      visual,
      semantic,
      affordances,
    };
  }

  private computeConfidence(
    axElem: AccessibilityElement,
    ocr: OCRResult | null,
    yolo: YOLOResult | null
  ): number {
    let confidence = 0;

    if (axElem.role) {
      confidence += (this.config.confidenceWeights.accessibility ?? 0.9) * 0.6;
    }

    if (ocr && ocr.confidence !== undefined) {
      confidence += (this.config.confidenceWeights.ocr ?? 0.7) * 0.25 * ocr.confidence;
    }

    if (yolo && yolo.confidence !== undefined) {
      confidence += (this.config.confidenceWeights.yolo ?? 0.8) * 0.15 * yolo.confidence;
    }

    return Math.min(1, confidence);
  }

  private inferAffordances(type: UIElement["type"], axElem: AccessibilityElement): UIElement["affordances"] {
    const baseAffordances: UIElement["affordances"] = {
      clickable: false,
      editable: false,
      scrollable: false,
      draggable: false,
      keyboardFocusable: false,
    };

    if (type === "button" || type === "menu" || type === "icon") {
      baseAffordances.clickable = true;
      baseAffordances.keyboardFocusable = true;
    }

    if (type === "input" || type === "text") {
      baseAffordances.editable = true;
      baseAffordances.keyboardFocusable = true;
    }

    if (type === "panel" || type === "unknown") {
      baseAffordances.scrollable = true;
    }

    if (axElem.state) {
      if (axElem.state.focused) baseAffordances.keyboardFocusable = true;
      if (axElem.state.enabled === false) {
        baseAffordances.clickable = false;
        baseAffordances.editable = false;
      }
    }

    return baseAffordances;
  }

  private computeOverlap(a: BoundingBox, b: BoundingBox): number {
    const xOverlap = Math.max(0, Math.min(a.x + a.width, b.x + b.width) - Math.max(a.x, b.x));
    const yOverlap = Math.max(0, Math.min(a.y + a.height, b.y + b.height) - Math.max(a.y, b.y));

    if (xOverlap === 0 || yOverlap === 0) return 0;

    const overlapArea = xOverlap * yOverlap;
    const aArea = a.width * a.height;
    const bArea = b.width * b.height;
    const minArea = Math.min(aArea, bArea);

    return overlapArea / minArea;
  }

  private mergeOverlappingElements(elements: UIElement[]): UIElement[] {
    const merged: UIElement[] = [];
    const used = new Set<number>();

    for (let i = 0; i < elements.length; i++) {
      if (used.has(i)) continue;

      const current = elements[i];
      const toMerge: number[] = [];

      for (let j = i + 1; j < elements.length; j++) {
        if (used.has(j)) continue;

        const overlap = this.computeOverlap(current.bounds, elements[j].bounds);
        if (overlap > 0.7) {
          toMerge.push(j);
        }
      }

      if (toMerge.length > 0) {
        const allBounds = [current.bounds, ...toMerge.map((j) => elements[j].bounds)];
        const mergedBounds = this.unionBounds(allBounds);

        merged.push({
          ...current,
          id: current.id,
          bounds: mergedBounds,
          visual: {
            ...current.visual,
            textOcr: [current.visual.textOcr, ...toMerge.map((j) => elements[j].visual.textOcr)]
              .filter(Boolean)
              .join(" "),
          },
        });

        for (const j of toMerge) {
          used.add(j);
        }
      } else {
        merged.push(current);
      }

      used.add(i);
    }

    return merged;
  }

  private unionBounds(bounds: BoundingBox[]): BoundingBox {
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

  private mapRoleToType(role: string): UIElement["type"] {
    const roleLower = role.toLowerCase();

    if (roleLower.includes("button")) return "button";
    if (roleLower.includes("text") && roleLower.includes("field")) return "input";
    if (roleLower.includes("text") && roleLower.includes("area")) return "input";
    if (roleLower.includes("menu")) return "menu";
    if (roleLower.includes("panel")) return "panel";
    if (roleLower.includes("toolbar")) return "toolbar";
    if (roleLower.includes("dialog")) return "dialog";
    if (roleLower.includes("icon")) return "icon";
    if (roleLower.includes("list")) return "panel";
    if (roleLower.includes("table")) return "panel";
    if (roleLower.includes("window")) return "panel";

    return "unknown";
  }

  private mapClassToType(className: string): UIElement["type"] {
    const classLower = className.toLowerCase();

    if (classLower.includes("button")) return "button";
    if (classLower.includes("input")) return "input";
    if (classLower.includes("menu")) return "menu";
    if (classLower.includes("icon")) return "icon";
    if (classLower.includes("dialog")) return "dialog";
    if (classLower.includes("toolbar")) return "toolbar";
    if (classLower.includes("panel")) return "panel";
    if (classLower.includes("window")) return "panel";

    return "unknown";
  }

  private inferTypeFromText(text: string): UIElement["type"] {
    const textLower = text.toLowerCase().trim();

    if (textLower === "ok" || textLower === "cancel" || textLower === "yes" || textLower === "no") {
      return "button";
    }

    if (textLower === "submit" || textLower === "save" || textLower === "delete" || textLower === "close") {
      return "button";
    }

    if (text.length < 50 && !text.includes(".") && !text.includes("\n")) {
      return "button";
    }

    return "text";
  }
}

export class OCREngine {
  async recognizeText(imageData: Buffer): Promise<OCRResult[]> {
    return this.fallbackOCR(imageData);
  }

  private async fallbackOCR(imageData: Buffer): Promise<OCRResult[]> {
    const { exec } = await import("child_process");
    const { promisify } = await import("util");
    const execAsync = promisify(exec);

    const tmpFile = `/tmp/oscar_ocr_${Date.now()}.png`;
    const outputFile = `/tmp/oscar_ocr_${Date.now()}.txt`;

    try {
      require("fs").writeFileSync(tmpFile, imageData);

      const { stdout } = await execAsync(
        `sips -s format png ${tmpFile} --out ${tmpFile} 2>/dev/null; tesseract ${tmpFile} ${outputFile.replace('.txt', '')} -l osd 2>/dev/null || echo "TESSERACT_FAILED"`,
        { timeout: 10000 }
      );

      if (stdout.includes("TESSERACT_FAILED")) {
        return this.nativeMacOCR();
      }

      const text = require("fs").readFileSync(outputFile, "utf-8");
      return this.parseOCRText(text, imageData);
    } catch {
      return this.nativeMacOCR();
    } finally {
      try {
        require("fs").unlinkSync(tmpFile);
        require("fs").unlinkSync(outputFile);
      } catch {}
    }
  }

  private async nativeMacOCR(): Promise<OCRResult[]> {
    return [];
  }

  private parseOCRText(text: string, _imageData: Buffer): OCRResult[] {
    const results: OCRResult[] = [];

    const lines = text.split("\n").filter((line) => line.trim().length > 0);

    let y = 0;
    const lineHeight = 20;

    for (const line of lines) {
      const trimmed = line.trim();
      if (trimmed.length > 0) {
        results.push({
          text: trimmed,
          bounds: {
            x: 10,
            y,
            width: trimmed.length * 8,
            height: lineHeight,
          },
          confidence: 0.7,
        });
      }
      y += lineHeight;
    }

    return results;
  }
}

export class YOLOEngine {
  async detect(imageData: Buffer): Promise<YOLOResult[]> {
    return this.ruleBasedDetection(imageData);
  }

  private async ruleBasedDetection(_imageData: Buffer): Promise<YOLOResult[]> {
    return [];
  }
}

export class AccessibilityTreeParser {
  parse(rawTree: AccessibilityElement[]): AccessibilityElement[] {
    return rawTree.filter((elem) => this.isMeaningfulElement(elem));
  }

  private isMeaningfulElement(elem: AccessibilityElement): boolean {
    if (!elem.role) return false;

    const role = elem.role.toLowerCase();

    const ignoreRoles = [
      "scrollbar",
      "menu bar",
      "statusbar",
      "progress indicator",
      "spinbutton",
      "separator",
    ];

    for (const ignore of ignoreRoles) {
      if (role.includes(ignore)) return false;
    }

    return true;
  }

  enrichWithContext(elements: AccessibilityElement[]): AccessibilityElement[] {
    return elements.map((elem, index) => ({
      ...elem,
      id: elem.id || `elem_${index}`,
    }));
  }
}
