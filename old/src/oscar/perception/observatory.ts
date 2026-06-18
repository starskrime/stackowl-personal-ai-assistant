import type {
  UIElement,
  ScreenGraph,
  AccessibilityState,
  BoundingBox,
  Point,
} from "../types.js";
import { TripleBufferPipeline } from "./pipeline.js";
import { ScreenGraphBuilder } from "./screen-graph.js";
import { PerceptionFusion, OCREngine, YOLOEngine, AccessibilityTreeParser } from "./fusion.js";
import { RegionClassifier, VisualPatternLearner } from "./region-classifier.js";
import { QueryEngine, QueryBuilder } from "./query-engine.js";
import { macOSAdapter } from "../platform/adapters/macos.js";

export interface ObservatoryConfig {
  enableOCR?: boolean;
  enableYOLO?: boolean;
  enableRegionClassification?: boolean;
  enableVisualLearning?: boolean;
  perceptionInterval?: number;
}

export interface ObservationResult {
  graph: ScreenGraph;
  elements: UIElement[];
  regions: import("../types.js").Region[];
  timestamp: number;
  focus: { app: string; element: string | null; cursor: Point };
  queryEngine: QueryEngine;
}

export class ScreenGraphObservatory {
  private config: Required<ObservatoryConfig>;
  private pipeline: TripleBufferPipeline;
  private fusion: PerceptionFusion;
  private ocrEngine: OCREngine;
  private yoloEngine: YOLOEngine;
  private axParser: AccessibilityTreeParser;
  private regionClassifier: RegionClassifier;
  private patternLearner: VisualPatternLearner;
  private lastGraph: ScreenGraph | null = null;
  private running = false;

  constructor(config: ObservatoryConfig = {}) {
    this.config = {
      enableOCR: config.enableOCR ?? true,
      enableYOLO: config.enableYOLO ?? false,
      enableRegionClassification: config.enableRegionClassification ?? true,
      enableVisualLearning: config.enableVisualLearning ?? true,
      perceptionInterval: config.perceptionInterval ?? 100,
    };

    this.pipeline = new TripleBufferPipeline(this.config.perceptionInterval);
    this.fusion = new PerceptionFusion();
    this.ocrEngine = new OCREngine();
    this.yoloEngine = new YOLOEngine();
    this.axParser = new AccessibilityTreeParser();
    this.regionClassifier = new RegionClassifier();
    this.patternLearner = new VisualPatternLearner();
  }

  async start(): Promise<void> {
    if (this.running) return;
    this.running = true;
    console.log("[Observatory] Screen Graph Observatory started");
  }

  stop(): void {
    this.running = false;
    this.pipeline.stop();
    console.log("[Observatory] Screen Graph Observatory stopped");
  }

  async observe(): Promise<ObservationResult> {
    const [screenBuffer, accessibilityState, app] = await Promise.all([
      this.pipeline.captureRegion({ x: 0, y: 0, width: 1920, height: 1080 }),
      macOSAdapter.getAccessibilityTree(),
      macOSAdapter.getFocusedApp(),
    ]);

    const focus = {
      app: app || "unknown",
      element: accessibilityState.focusedElement || null,
      cursor: { x: 0, y: 0 },
    };

    const elements = await this.perceiveElements(accessibilityState, screenBuffer.imageData);

    const resolution = {
      width: screenBuffer.width || 1920,
      height: screenBuffer.height || 1080,
    };

    const builder = new ScreenGraphBuilder().setResolution(resolution.width, resolution.height);

    builder.addElements(elements);

    if (this.config.enableRegionClassification) {
      const regions = this.regionClassifier.classify(
        new Map(elements.map((e) => [e.id, e])),
        builder.getIndex(),
        resolution
      );

      for (const region of regions) {
        if (this.config.enableVisualLearning) {
          const regionElements = region.elements
            .map((id) => elements.find((e) => e.id === id))
            .filter((e): e is UIElement => e !== undefined);
          this.patternLearner.learnFromRegion(region, regionElements);
        }
      }
    }

    builder.computeRelationships();

    const graph = builder.build(focus);

    this.lastGraph = graph;

    return {
      graph,
      elements,
      regions: graph.regions,
      timestamp: graph.timestamp,
      focus,
      queryEngine: new QueryEngine(graph),
    };
  }

  async perceiveFromState(accessibilityState: AccessibilityState): Promise<UIElement[]> {
    return this.perceiveElements(accessibilityState, Buffer.alloc(0));
  }

  private async perceiveElements(
    accessibilityState: AccessibilityState,
    imageData: Buffer
  ): Promise<UIElement[]> {
    const axElements = this.axParser.parse(
      this.axParser.enrichWithContext(Array.from(accessibilityState.elements.values()))
    );

    let ocrResults: import("./fusion.js").OCRResult[] = [];
    let yoloResults: import("./fusion.js").YOLOResult[] = [];

    if (this.config.enableOCR && imageData.length > 0) {
      try {
        ocrResults = await this.ocrEngine.recognizeText(imageData);
      } catch (error) {
        console.warn("[Observatory] OCR failed:", error);
      }
    }

    if (this.config.enableYOLO && imageData.length > 0) {
      try {
        yoloResults = await this.yoloEngine.detect(imageData);
      } catch (error) {
        console.warn("[Observatory] YOLO detection failed:", error);
      }
    }

    return this.fusion.fuse(axElements, ocrResults, yoloResults);
  }

  async observeDelta(): Promise<{
    added: UIElement[];
    removed: UIElement[];
    unchanged: UIElement[];
    graph: ScreenGraph;
  } | null> {
    if (!this.lastGraph) {
      const result = await this.observe();
      return {
        added: result.elements,
        removed: [],
        unchanged: [],
        graph: result.graph,
      };
    }

    const current = await this.observe();

    const previousIds = new Set(this.lastGraph.elements.keys());
    const currentIds = new Set(current.graph.elements.keys());

    const added: UIElement[] = [];
    const removed: UIElement[] = [];
    const unchanged: UIElement[] = [];

    for (const elem of current.elements) {
      if (!previousIds.has(elem.id)) {
        added.push(elem);
      } else {
        unchanged.push(elem);
      }
    }

    for (const id of previousIds) {
      if (!currentIds.has(id)) {
        const elem = this.lastGraph.elements.get(id);
        if (elem) removed.push(elem);
      }
    }

    return {
      added,
      removed,
      unchanged,
      graph: current.graph,
    };
  }

  query(): QueryBuilder | null {
    if (!this.lastGraph) return null;
    return new QueryBuilder(this.lastGraph);
  }

  getLastGraph(): ScreenGraph | null {
    return this.lastGraph;
  }

  async captureRegion(bounds: BoundingBox): Promise<Buffer> {
    const buffer = await this.pipeline.captureRegion(bounds);
    return buffer.imageData;
  }

  getRegionClassifier(): RegionClassifier {
    return this.regionClassifier;
  }

  getPatternLearner(): VisualPatternLearner {
    return this.patternLearner;
  }

  isRunning(): boolean {
    return this.running;
  }
}

export const observatory = new ScreenGraphObservatory();

export function createObservatory(config?: ObservatoryConfig): ScreenGraphObservatory {
  return new ScreenGraphObservatory(config);
}
