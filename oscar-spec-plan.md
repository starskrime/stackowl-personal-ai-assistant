# OSCAR Implementation Plan: Architectural Magic

## Philosophy

Each phase delivers **architectural innovations** — not just working code, but **novel computational systems** that enable OSCAR to perceive, reason, and act intelligently. This plan emphasizes the *how* and *why* behind each architectural decision.

---

## Phase 1: The Universal Control Interface (UCI)

### **Architectural Magic: Platform Abstraction Without Emulation**

The core insight: **OSCAR should not emulate human input** — it should provide a unified control interface that abstracts away platform differences while preserving native performance.

### 1.1 Unified Control Bus (UCB)

**Problem:** macOS uses AXUIElement, Windows uses UIAutomation, Linux uses AT-SPI2. Naive approach: write 3 separate codebases.

**Solution:** A message-passing architecture where platform-specific adapters emit a **canonical action protocol**:

```typescript
// All platforms emit the same canonical format
interface CanonicalAction {
  type: "click" | "type" | "drag" | "scroll" | "invoke" | "observe";
  target: CanonicalTarget;
  params: Record<string, unknown>;
  timestamp: number;
  trace_id: string;
}

interface CanonicalTarget {
  app_bundle_id?: string;      // macOS bundle ID, Windows exe name, Linux desktop file
  window_title?: string;       // For disambiguation
  accessibility_path?: string; // Platform-native path
  visual_region?: BoundingBox; // Fallback coordinates
  semantic_selector?: {        // Human-readable selector
    role?: string;
    label?: string;
    index?: number;
  };
}
```

**Architectural Innovation:** The **Canonical Target Resolver** — a three-tier lookup that tries:
1. **Accessibility path** (fast, precise, if available)
2. **Semantic selector** (infers from role + label + index)
3. **Visual region** (fallback to coordinates with retry)

If Tier 1 fails → try Tier 2 → try Tier 3 → report failure. This means OSCAR works even on apps with broken accessibility.

### 1.2 The Screenshot Pipeline: Latency Hiding

**Problem:** Screenshot → encode → send to model = 100ms+ latency. User notices lag.

**Solution:** **Triple-buffered pipeline**:

```
┌──────────────────────────────────────────────────────────────────┐
│                     SCREENSHOT PIPELINE                          │
│                                                                  │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐          │
│  │ Buffer A    │───→│ Buffer B    │───→│ Buffer C    │          │
│  │ (Capture)   │    │ (Encode)    │    │ (Ready)     │          │
│  └─────────────┘    └─────────────┘    └─────────────┘          │
│         │                │                │                      │
│         │      ┌─────────┴──────┐         │                      │
│         │      │                │         │                      │
│         ▼      ▼                ▼         ▼                      │
│  [Async Capture] [BG Encode]   [Model Input]                     │
│                                                                  │
│  WHILE model processes Buffer C:                                  │
│    Buffer A is being captured (async)                            │
│    Buffer B is being encoded (async)                             │
│                                                                  │
│  RESULT: Perceived latency ≈ 0ms (pipeline full)                │
└──────────────────────────────────────────────────────────────────┘
```

**Key Insight:** The model always receives a "fresh" screenshot because we start the next capture *immediately* after delivering to model, not after processing completes.

**Implementation:**
```typescript
class TripleBufferPipeline {
  private buffers: [ScreenBuffer, ScreenBuffer, ScreenBuffer];
  private writeIdx = 0;
  private readIdx = 2;
  
  async capture(): Promise<void> {
    // Never blocks - writes to "write" buffer while others are processed
    const buf = this.buffers[this.writeIdx];
    await buf.capture();           // Async screen capture
    await buf.encode();            // Async JPEG/WebP encode
    this.writeIdx = (this.writeIdx + 1) % 3;
  }
  
  getLatest(): ScreenBuffer {
    return this.buffers[(this.writeIdx + 2) % 3]; // Latest completed
  }
}
```

### 1.3 The Verification Micro-Engine

**Problem:** How do we verify "button clicked" without expensive vision model?

**Solution:** **Delta Tree Comparison** — Compare accessibility trees before/after:

```typescript
async verify(expectedState: AccessibilityState): Promise<VerificationResult> {
  const before = await this.getAccessibilityTree();
  
  await this.execute(action);
  
  // Poll for changes (10ms interval, 2s timeout)
  for (let attempt = 0; attempt < 200; attempt++) {
    const after = await this.getAccessibilityTree();
    const delta = computeDelta(before, after);
    
    if (matchesExpected(delta, expectedState)) {
      return { success: true, delta, attempts: attempt + 1 };
    }
    
    // Detect failure patterns
    if (this.isErrorState(after)) {
      return { success: false, error: this.extractError(after) };
    }
    
    await sleep(10);
  }
  
  return { success: false, reason: "timeout" };
}
```

**Delta Types:**
- `element_focused`: Button now has focus
- `children_added/removed`: Dialog opened/closed
- `attribute_changed`: Element state changed
- `window_opened/closed`: New window detected

**Architectural Magic:** The delta computation uses a **longest-common-subsequence** algorithm on the accessibility tree, giving us minimal diff that precisely captures what changed.

### 1.4 Deliverables

| Component | Architecture | Magic |
|-----------|-------------|-------|
| `oscar/platform/adapters/macos.ts` | AXUIElement → Canonical Action | 3-tier target resolution |
| `oscar/platform/adapters/windows.ts` | UIAutomation → Canonical Action | COM bridge with async |
| `oscar/platform/adapters/linux.ts` | AT-SPI2 → Canonical Action | D-Bus proxy with caching |
| `oscar/perception/pipeline.ts` | Triple-buffer async pipeline | Latency hiding |
| `oscar/verification/micro-engine.ts` | Delta tree comparison | LCS-based diff |

### 1.5 Success Criteria

- Launch app → button click works on all 3 platforms
- Screenshot latency: <16ms (60fps pipeline)
- Verification accuracy: >95% for accessibility-based checks
- Fallback to visual when accessibility unavailable

---

## Phase 2: The Screen Graph Observatory (SGO)

### **Architectural Magic: Representing Screens as Queryable Knowledge Graphs**

The core insight: **A screenshot is not data — it's a photograph.** To reason about screens, OSCAR needs a **structured representation** that supports queries, similarity, and traversal.

### 2.1 The Screen Graph Data Structure

**Problem:** Screenshots are pixel blobs — no structure, no searchability.

**Solution:** The **Screen Graph** — a hybrid spatial-semantic graph:

```typescript
class ScreenGraph {
  id: string;
  timestamp: number;
  resolution: Resolution;
  
  // Core graph (graphology instance)
  graph: Graph;  // Nodes = UI elements, Edges = relationships
  
  // Spatial index (R-tree for fast region queries)
  spatialIndex: RBush;  // Min Max query for "find elements in region"
  
  // Semantic index (Inverted index for text search)
  textIndex: Map<string, string[]>;  // word → [element_id, ...]
  
  // Semantic layers (overlay computed metadata)
  layers: {
    regions: RegionOverlay;      // Toolbar, sidebar, content, dialog
    focus: FocusChain;           // Tab order / accessibility focus
    interaction: InteractionHint; // "most likely next click"
  };
}
```

**Graph Schema:**

```typescript
interface ScreenGraphNode {
  id: string;
  
  // Visual properties (from screenshot analysis)
  visual: {
    bounds: BoundingBox;
    icon_embedding?: number[];   // CLIP embedding for icon matching
    style: VisualStyle;         // colors, fonts, shadows
  };
  
  // Semantic properties (from accessibility + inference)
  semantic: {
    role: "button" | "input" | "menu" | "panel" | "toolbar" | "unknown";
    label: string;
    description?: string;
    state: Record<string, boolean>;
    keyboard_shortcut?: string;
  };
  
  // Spatial relationships (computed, not stored)
  spatial: {
    parent?: string;
    children: string[];
    siblings: string[];
    overlaps: string[];        // Elements that visually overlap
    z_order: number;
  };
  
  // History (for learning)
  history: Interaction[];
}
```

**Edges (computed from spatial + semantic analysis):**

| Edge Type | Construction | Meaning |
|-----------|-------------|---------|
| `contains` | Bounds containment | Panel contains button |
| `siblings` | Same parent, similar Y | Toolbar items |
| `overlaps` | Bounds intersection > 50% | Modal over content |
| `z_order` | Render order | Which element is on top |
| `tab_chain` | Focus traversal order | Tab navigation path |
| `temporal` | Same location across frames | Persistent element |

### 2.2 The Perception Stack: From Pixels to Graph

**Architecture:**

```
┌─────────────────────────────────────────────────────────────────────┐
│                      PERCEPTION STACK                               │
│                                                                     │
│  ┌─────────────┐                                                    │
│  │ Screenshot  │                                                    │
│  └──────┬──────┘                                                    │
│         │                                                           │
│         ▼                                                           │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │                    PARALLEL PROCESSING                        │  │
│  │                                                               │  │
│  │  ┌───────────────┐  ┌───────────────┐  ┌────────────────┐   │  │
│  │  │ OCR Pipeline  │  │ YOLO Detection│  │ AX Tree Parse │   │  │
│  │  │               │  │               │  │                │   │  │
│  │  │ • Text regions│  │ • Bounding box│  │ • Role/labels  │   │  │
│  │  │ • Text content│  │ • Confidence  │  │ • States       │   │  │
│  │  │ • Bounding    │  │ • Class       │  │ • Hierarchy    │   │  │
│  │  └───────────────┘  └───────────────┘  └────────────────┘   │  │
│  │                                                               │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                              │                                     │
│                              ▼                                     │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │                 FUSION ENGINE                                 │  │
│  │                                                               │  │
│  │  1. Spatial Join: Match detections to AX elements by bounds  │  │
│  │  2. Label Propagation: Use OCR to verify/fill AX labels     │  │
│  │  3. Role Refinement: Use visual features to correct roles   │  │
│  │  4. State Inference: Use visual state (colors) to infer      │  │
│  │                                                               │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                              │                                     │
│                              ▼                                     │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │                 GRAPH BUILDER                                 │  │
│  │                                                               │  │
│  │  1. Create nodes from fused elements                        │  │
│  │  2. Compute spatial relationships (R-tree + intersection)    │  │
│  │  3. Extract semantic relationships (parent-child, siblings)   │  │
│  │  4. Build inverted indices (text, spatial, semantic)         │  │
│  │  5. Classify regions (toolbar via heuristics + learning)    │  │
│  │                                                               │  │
│  └──────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

**Fusion Algorithm: Spatial Join with Confidence Weighting**

```typescript
function fuse(ocrResults, yoloDetections, axTree) {
  // Start with accessibility tree as source of truth for labels/states
  const elements = axTree.map(ax => ({
    id: generateId(),
    ax_data: ax,
    visual: { bounds: ax.bounds },
    confidence: 1.0
  }));
  
  // For each OCR result, find matching AX element by bounds overlap
  for (const ocr of ocrResults) {
    const match = findBestOverlap(ocr.bounds, elements);
    if (match && overlap > 0.7) {
      match.text = ocr.text;
      match.confidence *= 0.9;  // Slight penalty for multi-source
    } else {
      // OCR-only element (e.g., canvas content)
      elements.push({ id: generateId(), text: ocr.text, visual: { bounds: ocr.bounds }, confidence: 0.7 });
    }
  }
  
  // For each YOLO detection, find match or create new element
  for (const det of yoloDetections) {
    const match = findBestOverlap(det.bounds, elements);
    if (match && overlap > 0.6) {
      match.visual.type = det.class;
      match.confidence *= 0.9;
    } else {
      // YOLO-only element (e.g., custom rendered content)
      elements.push({ id: generateId(), visual: { type: det.class, bounds: det.bounds }, confidence: 0.6 });
    }
  }
  
  return elements;
}
```

### 2.3 Region Classification via Learned Heuristics

**Problem:** Identifying "toolbar" vs "sidebar" vs "content" is nontrivial.

**Solution:** **Cascaded Classifier**:

```typescript
interface RegionClassifier {
  // Stage 1: Rule-based (fast, covers 80%)
  rules: Array<(graph: ScreenGraph, region: Region) => Classification | null>;
  
  // Stage 2: Heuristic scoring (covers 15%)
  scoreRegions(graph: ScreenGraph): Map<string, RegionScore>;
  
  // Stage 3: Neural (covers remaining 5%, most ambiguous)
  neuralClassify(image: Image, regions: Region[]): Promise<Map<string, Classification>>;
}

const regionRules = [
  // Rule: Top horizontal strip with icons = toolbar
  (g, r) => {
    if (r.isTopStrip() && r.avgChildSize() < 50 && r.childCount() > 3) {
      return { type: "toolbar", confidence: 0.9 };
    }
    return null;
  },
  
  // Rule: Left vertical strip with large icons = sidebar
  (g, r) => {
    if (r.isLeftStrip() && r.avgChildSize() < 80 && r.orientation() === "vertical") {
      return { type: "sidebar", confidence: 0.85 };
    }
    return null;
  },
  
  // Rule: Modal dialog has overlay behind it
  (g, r) => {
    if (g.hasOverlayBehind(r) && r.childCount() < 10) {
      return { type: "dialog", confidence: 0.8 };
    }
    return null;
  },
];
```

### 2.4 Spatial Query Engine

**Why:** OSCAR needs to answer queries like "find the OK button in the dialog" or "find all buttons in the toolbar region".

**Solution:** **R-tree + semantic index hybrid**:

```typescript
class SpatialQueryEngine {
  constructor(private graph: ScreenGraph) {
    // Build R-tree for bounding box queries
    this.rtree = new RBush();
    for (const node of graph.nodes()) {
      this.rtree.insert(node.visual.bounds);
    }
    
    // Build inverted index for text search
    this.textIndex = new Map();
    for (const node of graph.nodes()) {
      const words = tokenize(node.semantic.label || "");
      for (const word of words) {
        addToMap(this.textIndex, word, node.id);
      }
    }
  }
  
  // Query: Find clickable elements in a region
  findInRegion(bounds: BoundingBox): ScreenGraphNode[] {
    const candidates = this.rtree.search(bounds);
    return candidates.filter(n => n.affordances.clickable);
  }
  
  // Query: Find element by label (fuzzy)
  findByLabel(label: string): ScreenGraphNode[] {
    const normalized = normalize(label);
    const candidates = this.textIndex.get(normalized) || [];
    return candidates
      .map(id => this.graph.node(id))
      .filter(n => fuzzyMatch(normalize(n.label), normalized) > 0.8);
  }
  
  // Query: Find element by semantic role
  findByRole(role: string): ScreenGraphNode[] {
    return this.graph.nodes().filter(n => n.semantic.role === role);
  }
}
```

### 2.5 Deliverables

| Component | Architecture | Magic |
|-----------|-------------|-------|
| `oscar/perception/screen-graph.ts` | Graph + R-tree + inverted index | Hybrid spatial-semantic queries |
| `oscar/perception/fusion.ts` | Parallel OCR + YOLO + AX merge | Confidence-weighted spatial join |
| `oscar/perception/region-classifier.ts` | Cascaded rules + heuristics + neural | 3-stage classification |
| `oscar/perception/query-engine.ts` | R-tree spatial index | Fast region queries |
| `oscar/perception/detection/` | YOLO model runner (ONNX) | Real-time element detection |

### 2.6 Success Criteria

- Screen Graph constructed in <50ms (including YOLO)
- >90% element detection accuracy vs manual annotation
- Region classification: >85% accuracy
- Query latency: <5ms for region/text/role queries

---

## Phase 3: The Intent Processing Engine (IPE)

### **Architectural Magic: Translating Human Intent into Executable Plans**

The core insight: **Humans say what they want, not how to do it.** OSCAR needs to bridge the semantic gap between "clear my background" and "click the Magic Eraser tool".

### 3.1 The Intent Decomposition Pipeline

```
┌─────────────────────────────────────────────────────────────────────┐
│                    INTENT DECOMPOSITION PIPELINE                     │
│                                                                     │
│  User Input: "Help me clear the background in this product photo"  │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │ STAGE 1: Intent Parsing (LLM)                               │  │
│  │                                                              │  │
│  │ Input: "Help me clear the background in this product photo"  │  │
│  │                                                              │  │
│  │ Output: {                                                    │  │
│  │   verb: "clear",                                             │  │
│  │   object: "background",                                      │  │
│  │   context: {                                                 │  │
│  │     file: "product_photo.psd",                                │  │
│  │     implied_app: "photoshop",                               │  │
│  │     expected_outcome: "transparent background"               │  │
│  │   }                                                          │  │
│  │ }                                                            │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                              │                                     │
│                              ▼                                     │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │ STAGE 2: Semantic Action Mapping (Knowledge Graph)           │  │
│  │                                                              │  │
│  │ "clear.background" → SAS.edit.clear (primary)               │  │
│  │                    → SAS.filter.remove (synonym)             │  │
│  │                    → SAS.layer.delete (implementation)       │  │
│  │                                                              │  │
│  │ + App-specific knowledge:                                    │  │
│  │   "Photoshop has: Magic Eraser, Background Eraser,          │  │
│  │    Select > Subject, Refine Edge, Layer delete"              │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                              │                                     │
│                              ▼                                     │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │ STAGE 3: Execution Plan Generation (DAG Builder)            │  │
│  │                                                              │  │
│  │  ┌────────────┐                                             │  │
│  │  │ Node 1:    │                                             │  │
│  │  │ tool.select│ ←──────────────┐                           │  │
│  │  │ (magic_eraser)              │                           │  │
│  │  └────────────┘                     │                      │  │
│  │        │                              │                      │  │
│  │        ▼                              │                      │  │
│  │  ┌────────────┐                     │                      │  │
│  │  │ Node 2:    │ ←── depends on ──┘   │                      │  │
│  │  │ tool.adjust│ (tolerance=32)       │                      │  │
│  │  └────────────┘                     │                      │  │
│  │        │                              │                      │  │
│  │        ▼                              │                      │  │
│  │  ┌────────────┐                     │                      │  │
│  │  │ Node 3:    │ ←── depends on ──┘   │                      │  │
│  │  │ canvas.click                      │                      │  │
│  │  │ (background_pixels)               │                      │  │
│  │  └────────────┘                     │                      │  │
│  │        │                              │                      │  │
│  │        ▼                              │                      │  │
│  │  ┌────────────┐                     │                      │  │
│  │  │ Node 4:    │ ←── depends on ──┘   │                      │  │
│  │  │ key.press  │                      │                      │  │
│  │  │ (Delete)    │                      │                      │  │
│  │  └────────────┘                      │                      │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                              │                                     │
│                              ▼                                     │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │ STAGE 4: Verification Condition Generation                   │  │
│  │                                                              │  │
│  │ Node 1 → verify: "tool.active(icon='magic_eraser')"         │  │
│  │ Node 2 → verify: "tolerance.value(=32)"                     │  │
│  │ Node 3 → verify: "selection.exists(marching_ants)"          │  │
│  │ Node 4 → verify: "layer.transparency(>90%)"                  │  │
│  └──────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

### 3.2 The Semantic Action Space Resolver

**Problem:** How does SAS.edit.clear map to concrete UI actions in *any* app?

**Solution:** **Hierarchical Resolution with Fallback**:

```typescript
class SASResolver {
  // Primary: App-specific knowledge base
  private appKnowledge: Map<string, AppKnowledge>;
  
  // Secondary: Visual pattern matching
  private visualMatcher: VisualMatcher;
  
  // Tertiary: Keyboard shortcut inference
  private shortcutInference: ShortcutInference;
  
  resolve(action: SemanticAction, context: ScreenGraph, app: AppInfo): ResolvedStep[] {
    // Try app-specific resolution first (most reliable)
    const appResolutions = this.appKnowledge.get(app.bundleId)?.resolve(action);
    if (appResolutions && appResolutions.isComplete()) {
      return appResolutions;
    }
    
    // Try visual pattern matching (for unknown apps)
    const visualResolutions = this.visualMatcher.findActionTargets(action, context);
    if (visualResolutions.length > 0) {
      return this.rankAndSelect(visualResolutions);
    }
    
    // Try keyboard shortcut inference
    const shortcutResolutions = this.shortcutInference.infer(action, context);
    if (shortcutResolutions.length > 0) {
      return shortcutResolutions;
    }
    
    // Fail with explanation
    throw new UnresolvableActionError(action, context);
  }
}
```

**App Knowledge Schema:**

```typescript
interface AppKnowledge {
  bundleId: string;
  version: string;  // Version-specific knowledge
  
  // Action mappings: semantic action → concrete UI operations
  actionMappings: {
    [semanticAction: string]: UIOperation[];  // Multiple paths for reliability
  };
  
  // Keyboard shortcuts
  shortcuts: {
    [action: string]: string;  // action → key combo
  };
  
  // UI patterns (toolbar locations, menu structures)
  patterns: {
    toolbarRegion?: BoundingBox;
    menuBarItems?: string[];
    statusBarLocation?: "top" | "bottom";
  };
  
  // Success rates (learned)
  actionStats: {
    [action: string]: {
      attempts: number;
      successes: number;
      lastAttempt?: number;
    };
  };
}
```

### 3.3 The DAG Validator and Optimizer

**Problem:** Generated plans might have circular dependencies, missing prerequisites, or inefficient sequences.

**Solution:** **Plan Validation + Optimization Pipeline**:

```typescript
class DAGValidator {
  validate(plan: ExecutionPlan): ValidationResult {
    const errors: ValidationError[] = [];
    
    // 1. Check for cycles
    if (this.hasCycle(plan)) {
      errors.push({ type: "cycle", nodes: this.findCycle(plan) });
    }
    
    // 2. Check for missing dependencies
    for (const node of plan.nodes) {
      for (const dep of node.dependsOn) {
        if (!plan.nodes.find(n => n.id === dep)) {
          errors.push({ type: "missing_dependency", node: node.id, missing: dep });
        }
      }
    }
    
    // 3. Check for parallelization opportunities
    const parallelizable = this.findParallelizable(plan);
    
    // 4. Estimate success probability
    const probability = this.estimateSuccess(plan);
    
    return { valid: errors.length === 0, errors, parallelizable, probability };
  }
  
  optimize(plan: ExecutionPlan): ExecutionPlan {
    // Reorder for parallelism: nodes with no dependencies between them can run concurrently
    const stages = this.topologicalSort(plan);
    return this.insertParallelStages(stages);
  }
}
```

### 3.4 The Recovery State Machine

**Problem:** Things go wrong during execution. How does OSCAR recover gracefully?

**Solution:** **Hierarchical Recovery with Strategy Selection**:

```typescript
// Recovery is not "try again" — it's a strategic decision
interface RecoveryStrategy {
  name: string;
  applicability: (failure: FailureContext) => number;  // 0-1 score
  execute: (failure: FailureContext, state: ExecutionState) => Promise<RecoveryResult>;
  rollback?: () => Promise<void>;
}

const recoveryStrategies: RecoveryStrategy[] = [
  // Strategy 1: Re-locate and retry (for "element not found")
  {
    name: "relocate_retry",
    applicability: (f) => f.type === "element_not_found" ? 0.9 : 0,
    async execute(failure, state) {
      // Re-scan screen for element with similar label/role
      const newTarget = await state.resolver.findSimilar(failure.target);
      return { replacement: newTarget, retry: true };
    }
  },
  
  // Strategy 2: Try alternative path (for "action failed")
  {
    name: "alternative_path",
    applicability: (f) => f.type === "action_failed" && f.alternatives.length > 0 ? 0.8 : 0,
    async execute(failure, state) {
      // Try the next alternative in the knowledge base
      const alt = failure.alternatives[0];
      return { replacement: alt, retry: true };
    }
  },
  
  // Strategy 3: Handle dialog (for "unexpected dialog")
  {
    name: "dialog_handler",
    applicability: (f) => f.type === "unexpected_dialog" ? 0.95 : 0,
    async execute(failure, state) {
      // Parse dialog content, decide: confirm/cancel/seek_help
      const response = await state.dialogParser.parse(failure.dialog);
      return { dialogResponse: response };
    }
  },
  
  // Strategy 4: Checkpoint restore (for unrecoverable states)
  {
    name: "checkpoint_restore",
    applicability: (f) => f.type === "critical_failure" && state.checkpoint ? 0.7 : 0,
    async execute(failure, state) {
      await state.checkpoint.restore();
      return { restarted: true };
    }
  },
  
  // Strategy 5: Escalate to user (for unknown failures)
  {
    name: "user_escalation",
    applicability: (f) => f.type === "unknown_error" ? 1.0 : 0,
    async execute(failure, state) {
      return { 
        requiresUser: true, 
        message: state.formatter.explain(failure),
        options: state.formatter.suggestAlternatives(failure)
      };
    }
  },
];

class RecoveryController {
  async handleFailure(failure: FailureContext, state: ExecutionState): Promise<RecoveryResult> {
    // Score all strategies by applicability
    const scores = this.strategies.map(s => ({
      strategy: s,
      score: s.applicability(failure)
    }));
    
    // Select highest-scoring strategy
    const best = scores.sort((a, b) => b.score - a.score)[0];
    
    if (best.score < 0.3) {
      // No good strategy — escalate to user
      return this.strategies.find(s => s.name === "user_escalation")!.execute(failure, state);
    }
    
    // Execute recovery
    return best.strategy.execute(failure, state);
  }
}
```

### 3.5 Checkpoint System: State快照

**Problem:** How do we restore state after a failure?

**Solution:** **Incremental State Diff with Serialization**:

```typescript
interface Checkpoint {
  id: string;
  planId: string;
  stepIndex: number;
  timestamp: number;
  
  // What we need to restore:
  appStates: Map<string, AppState>;  // Per-app state snapshots
  screenGraph: ScreenGraph;           // Visual state at checkpoint
  memoryState: MemorySnapshot;         // OSCAR's memory
  
  // For rollback:
  diffs: StateDiff[];  // Incremental diffs from previous checkpoint
}

class CheckpointManager {
  async create(state: ExecutionState, stepIndex: number): Promise<Checkpoint> {
    // Capture only what changed since last checkpoint (incremental)
    const lastCheckpoint = this.checkpoints.last();
    const diff = lastCheckpoint 
      ? computeDiff(lastCheckpoint, state)  // Only store delta
      : computeFullSnapshot(state);         // First checkpoint = full
    
    return {
      id: generateId(),
      planId: state.plan.id,
      stepIndex,
      timestamp: Date.now(),
      appStates: state.activeApps.map(app => [app.bundleId, app.captureState()]),
      screenGraph: state.screenGraph,
      memoryState: state.memory.snapshot(),
      diffs: lastCheckpoint ? [diff] : []
    };
  }
  
  async restore(checkpoint: Checkpoint): Promise<void> {
    // Reconstruct full state from base + diffs
    const baseState = checkpoint.diffs.length === 0 
      ? checkpoint 
      : this.reconstructFromDiffs(checkpoint);
    
    // Restore app states
    for (const [bundleId, appState] of baseState.appStates) {
      await this.appManager.restore(bundleId, appState);
    }
    
    // Restore memory
    baseState.memoryState.apply();
  }
}
```

### 3.6 Deliverables

| Component | Architecture | Magic |
|-----------|-------------|-------|
| `oscar/intent/parser.ts` | LLM-based intent extraction | Few-shot parsing with examples |
| `oscar/intent/decomposer.ts` | DAG builder with dependency analysis | Topological sort + parallelization |
| `oscar/intent/sas-resolver.ts` | Hierarchical resolution | App knowledge + visual + shortcuts |
| `oscar/intent/dag-validator.ts` | Cycle detection + optimization | Tarjan's algorithm + parallelization |
| `oscar/execution/recovery-controller.ts` | Strategy selection | Applicability scoring |
| `oscar/execution/checkpoint-manager.ts` | Incremental state diff | Binary diff for fast restore |

### 3.7 Success Criteria

- Intent parsing accuracy: >90% (verified against human annotation)
- Plan generation: <2 seconds for complex tasks (10+ steps)
- Recovery success: >80% of failures recovered autonomously
- Checkpoint size: <100KB per checkpoint (incremental)
- User escalation: Only when truly necessary (<5% of failures)

---

## Phase 4: The Visual Memory Network (VMN)

### **Architectural Magic: Learning, Remembering, and Generalizing Across Applications**

The core insight: **OSCAR should remember what it learns, so it never has to figure out the same thing twice.**

### 4.1 The Memory Architecture: Dual-Store with Cross-Indexing

**Problem:** Human memory has episodic (what happened) + semantic (what I know) + procedural (how to do things) components. OSCAR needs analogous structures.

**Solution:** **Three-Layer Memory Architecture**:

```
┌─────────────────────────────────────────────────────────────────────┐
│                      OSCAR MEMORY HIERARCHY                          │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  LAYER 1: EPISODIC MEMORY (Experience)                       │  │
│  │                                                              │  │
│  │  "I clicked the Magic Eraser in Photoshop at 2:34pm"          │  │
│  │  "User said 'no, that's wrong' and corrected me"             │  │
│  │  "The background removal worked, exported as PNG"            │  │
│  │                                                              │  │
│  │  Schema: Episode {                                           │  │
│  │    timestamp, app, actions[], outcome, user_feedback?       │  │
│  │  }                                                           │  │
│  │  Index: By app, by time, by outcome, by action_type         │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                              │                                     │
│                              │ abstraction                          │
│                              ▼                                     │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  LAYER 2: SEMANTIC MEMORY (Knowledge)                        │  │
│  │                                                              │  │
│  │  "Magic Eraser tool is in the toolbar, left side"            │  │
│  │  "To remove background: select tool → click bg → delete"    │  │
│  │  "Photoshop has 'Select > Subject' which works better"      │  │
│  │                                                              │  │
│  │  Schema: Affordance {                                        │  │
│  │    element_pattern, action, success_rate, alternatives[]    │  │
│  │  }                                                           │  │
│  │  Index: By element_type, by action, by app, by success       │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                              │                                     │
│                              │ composition                          │
│                              ▼                                     │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  LAYER 3: PROCEDURAL MEMORY (Skills)                          │  │
│  │                                                              │  │
│  │  "How to remove background in Photoshop"                     │  │
│  │  "How to debug Word document that won't open"                 │  │
│  │  "How to run regression tests in browser"                   │  │
│  │                                                              │  │
│  │  Schema: Skill {                                             │  │
│  │    name, description, steps[], prerequisites?, alternatives? │  │
│  │  }                                                           │  │
│  │  Index: By task_type, by app, by complexity                  │  │
│  └──────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

### 4.2 The Affordance Learning System

**Problem:** How does OSCAR learn that "this icon is a button" and "this button clears backgrounds"?

**Solution:** **Self-Supervised Affordance Extraction**:

```typescript
class AffordanceLearner {
  // After each action, record what worked
  async recordAffordance(
    screenGraph: ScreenGraph,
    action: Action,
    target: UIElement,
    outcome: Outcome
  ): Promise<void> {
    // Extract visual signature of the target element
    const signature = this.extractSignature(target, screenGraph);
    
    // Create affordance record
    const affordance: Affordance = {
      id: generateId(),
      visualSignature: signature,
      action: action.type,
      targetRole: target.semantic.role,
      targetLabel: target.semantic.label,
      app: currentApp().bundleId,
      success: outcome.success,
      timestamp: Date.now(),
    };
    
    // Store in semantic memory
    await this.memory.semantic.store(affordance);
    
    // Update success rate
    await this.updateSuccessRate(affordance);
  }
  
  // When encountering similar element, retrieve learned affordances
  async retrieveAffordances(
    element: UIElement,
    actionType: string
  ): Promise<Affordance[]> {
    // Find elements with similar visual signatures
    const candidates = await this.memory.semantic.findSimilar(
      element.visualSignature,
      { role: element.semantic.role }
    );
    
    // Filter to those that support the requested action
    return candidates.filter(a => a.action === actionType);
  }
}
```

**Visual Signature (for transfer learning):**

```typescript
interface VisualSignature {
  // Hashable representation for fast matching
  iconHash?: string;         // perceptual hash of icon (pHash)
  colorHistogram: number[];  // dominant colors
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
    region: "toolbar" | "sidebar" | "content" | "dialog";
    alignment: "left" | "center" | "right";
    relativeY: number;  // 0-1 normalized position
  };
}

// Similar signatures = likely similar affordances
function signatureDistance(a: VisualSignature, b: VisualSignature): number {
  return (
    (a.iconHash && b.iconHash ? pHashDistance(a.iconHash, b.iconHash) : 0.5) * 0.3 +
    colorHistogramDistance(a.colorHistogram, b.colorHistogram) * 0.2 +
    shapeDistance(a.shapeFeatures, b.shapeFeatures) * 0.3 +
    positionDistance(a.positionFeatures, b.positionFeatures) * 0.2
  );
}
```

### 4.3 Transfer Learning: Bootstrapping from Known Apps

**Problem:** OSCAR sees a new app. Can it leverage knowledge from apps it already knows?

**Solution:** **Cross-App Transfer via Affordance Graphs**:

```
┌─────────────────────────────────────────────────────────────────────┐
│                    TRANSFER LEARNING ARCHITECTURE                    │
│                                                                     │
│  KNOWN APPS:                    NEW APP:                             │
│                                                                     │
│  ┌─────────────┐                ┌─────────────┐                     │
│  │ Photoshop   │                │ GIMP        │                     │
│  │             │                │             │                     │
│  │ ✓ Magic Eraser│ ─────────── │ ? Magic Eraser│                    │
│  │ ✓ Background │              │ ? Background │                    │
│  │ ✓ Layers Panel│             │ ? Layers Panel│                   │
│  └─────────────┘                └─────────────┘                     │
│         │                                                         │
│         │ ABSTRACTION                                              │
│         ▼                                                         │
│  ┌─────────────────────────────────────────────────────────────┐  │
│  │  AFFORDANCE GRAPH (App-Agnostic Knowledge)                   │  │
│  │                                                               │  │
│  │  "Icon: wand/magic → tool: 'magic eraser' type"             │  │
│  │  "Tool location: toolbar, often top-left"                   │  │
│  │  "Action: 'clear background' → uses 'magic eraser'"         │  │
│  │  "Visual: checkerboard = transparent"                        │  │
│  │                                                               │  │
│  │  TRANSFER: GIMP's wand icon matches "magic eraser" pattern  │  │
│  │  → High confidence: GIMP's wand = clear background tool       │  │
│  └─────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

**Transfer Algorithm:**

```typescript
class TransferLearner {
  async transferTo(targetApp: AppInfo, targetElement: UIElement): Promise<Affordance[]> {
    // 1. Find abstract affordance patterns
    const patterns = await this.findMatchingPatterns(targetElement);
    
    // 2. Score each pattern by transferability
    const scored = patterns.map(p => ({
      pattern: p,
      score: this.computeTransferability(p, targetApp)
    }));
    
    // 3. Return top matches
    return scored
      .filter(s => s.score > THRESHOLD)
      .sort((a, b) => b.score - a.score)
      .map(s => this.instantiatePattern(s.pattern, targetApp));
  }
  
  computeTransferability(pattern: AffordancePattern, targetApp: AppInfo): number {
    // Factors:
    const iconSimilarity = pattern.iconHash 
      ? this.iconMatcher.match(pattern.iconHash, targetElement.iconHash)
      : 0.5;
    
    const roleMatch = pattern.targetRole === targetElement.semantic.role ? 1.0 : 0.7;
    
    const appSimilarity = this.appSimilarity(pattern.app, targetApp);
    
    // Apps in same category (photo editors) transfer better
    const categoryBonus = this.sameCategory(pattern.app, targetApp) ? 0.2 : 0;
    
    return (iconSimilarity * 0.4 + roleMatch * 0.3 + appSimilarity * 0.3 + categoryBonus);
  }
}
```

### 4.4 The Skill Composer: Learning Complex Workflows

**Problem:** Users want to teach OSCAR new tasks by demonstration.

**Solution:** **Recording + Generalization + Replay**:

```
┌─────────────────────────────────────────────────────────────────────┐
│                    SKILL COMPOSITION PIPELINE                        │
│                                                                     │
│  USER DEMONSTRATES:                                                 │
│                                                                     │
│  Step 1: Click "File" menu                                          │
│  Step 2: Click "Open"                                               │
│  Step 3: Navigate to folder                                         │
│  Step 4: Select file                                                │
│  Step 5: Click "Open" button                                        │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  RECORDING: Capture raw actions with exact coordinates        │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                              │                                     │
│                              ▼                                     │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  GENERALIZATION: Replace specifics with patterns             │  │
│  │                                                               │  │
│  │  "Click File menu" → Click(target: {role: "menu", label: *}) │  │
│  │  "Click Open" → Click(target: {role: "menuitem", label: *})  │  │
│  │  "Navigate to folder" → Navigate(path: *) (parameterized)   │  │
│  │  "Select file" → Select(target: {type: "file", name: *})    │  │
│  │  "Click Open button" → Click(target: {role: "button", label: "Open"}) │
│  └──────────────────────────────────────────────────────────────┘  │
│                              │                                     │
│                              ▼                                     │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  SKILL FORMULATION: Store as reusable workflow               │  │
│  │                                                               │  │
│  │  Skill {                                                      │  │
│  │    name: "open_document",                                     │  │
│  │    description: "Open a document via File menu",            │  │
│  │    parameters: [{ name: "filepath", type: "path" }],        │  │
│  │    steps: [generalized actions...],                         │  │
│  │    constraints: { app: ["*"], format: ["*"] },              │  │
│  │    success_conditions: { document_open: true }              │  │
│  │  }                                                           │  │
│  └──────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

**Generalization Algorithm:**

```typescript
class SkillComposer {
  generalize(recording: RawRecording): Skill {
    const generalizedSteps: GeneralizedStep[] = [];
    
    for (const raw of recording.steps) {
      const generalized = this.generalizeStep(raw);
      
      // Annotate parameterization
      if (this.isFilePath(raw.target)) {
        generalized.paramType = "filepath";
        generalized.paramHint = this.extractPathPattern(raw.target);
      } else if (this.isFolderPath(raw.target)) {
        generalized.paramType = "folderpath";
      } else if (this.isConstant(raw.target)) {
        generalized.isConstant = true;
      }
      
      generalizedSteps.push(generalized);
    }
    
    return {
      id: generateId(),
      steps: generalizedSteps,
      parameters: this.extractParameters(generalizedSteps),
      successConditions: this.inferSuccessConditions(recording),
    };
  }
  
  // Detect which targets are specific vs. generic
  private generalizeStep(raw: RawAction): GeneralizedStep {
    const target = raw.target;
    
    // If target has file path → parameterize
    if (this.isPath(target)) {
      return {
        action: raw.action,
        target: { type: "parameter", paramName: this.suggestParamName(target) },
        verification: raw.verification
      };
    }
    
    // If target is app-specific text → make flexible match
    if (target.label && this.looksLikeMenuItem(target.label)) {
      return {
        action: raw.action,
        target: { type: "flexible", role: target.role, labelPattern: target.label },
        verification: raw.verification
      };
    }
    
    // Keep as-is if it looks universal
    return { action: raw.action, target, verification: raw.verification };
  }
}
```

### 4.5 Deliverables

| Component | Architecture | Magic |
|-----------|-------------|-------|
| `oscar/memory/episodic-store.ts` | Time-series event store | Efficient timestamp queries |
| `oscar/memory/semantic-store.ts` | Affordance graph | Visual signature indexing |
| `oscar/memory/procedural-store.ts` | Skill repository | Parameterized workflow storage |
| `oscar/memory/affordance-learner.ts` | Self-supervised learning | Signature extraction + matching |
| `oscar/memory/transfer-learner.ts` | Cross-app knowledge transfer | Affordance graph traversal |
| `oscar/skills/composer.ts` | Recording → generalization | Generalization algorithm |
| `oscar/skills/replayer.ts` | Skill execution engine | Parameter binding + verification |

### 4.6 Success Criteria

- Affordance learning: >90% accuracy after 5 examples
- Transfer learning: >70% success when source app is similar
- Skill generalization: >80% reusability across apps
- Memory retrieval: <50ms for relevant memories
- Learning rate: Learn new app action in <3 attempts

---

## Phase 5: The Autonomous Cognitive Agent (ACA)

### **Architectural Magic: Self-Improving Intelligence That Anticipates Needs**

The core insight: **OSCAR should not just respond — it should anticipate, suggest, and improve itself.**

### 5.1 The Cognitive Loop: Observe → Reflect → Decide → Act → Learn

**Problem:** Most agents do "act and forget." OSCAR needs continuous self-improvement.

**Solution:** **Continuous Cognitive Cycle**:

```
┌─────────────────────────────────────────────────────────────────────┐
│                    OSCAR COGNITIVE LOOP                             │
│                                                                     │
│                        ┌─────────────────┐                          │
│                        │    OBSERVE      │                          │
│                        │                 │                          │
│                        │ • Screen state  │                          │
│                        │ • User actions  │                          │
│                        │ • Task progress │                          │
│                        │ • Time patterns │                          │
│                        └────────┬────────┘                          │
│                                 │                                    │
│                                 ▼                                    │
│                        ┌─────────────────┐                          │
│            ┌───────────│    REFLECT      │───────────┐              │
│            │           │                 │           │              │
│            │           │ • What worked? │           │              │
│            │           │ • What failed?  │           │              │
│            │           │ • What missing? │           │              │
│            │           │ • What new?     │           │              │
│            │           └────────┬────────┘           │              │
│            │                    │                      │              │
│            │    ┌───────────────┼───────────────┐     │              │
│            ▼    ▼               ▼               ▼     ▼              │
│     ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐         │
│     │ ANALYZE  │  │  LEARN    │  │  PLAN    │  │ ALERT    │         │
│     │          │  │          │  │          │  │          │         │
│     │ • Gaps   │  │ • Afford. │  │ • Self-  │  │ • User   │         │
│     │ • Errors │  │ • Skills  │  │   improve│  │   needs  │         │
│     │ • Pattrns│  │ • Memory  │  │ • Recover│  │ • Proac- │         │
│     │          │  │           │  │   yions   │  │   tive   │         │
│     └──────────┘  └──────────┘  └──────────┘  └──────────┘         │
│            │           │           │           │                     │
│            └───────────┴─────┬─────┴───────────┘                     │
│                              ▼                                        │
│                     ┌─────────────────┐                              │
│                     │      ACT        │                              │
│                     │                 │                              │
│                     │ • Execute plan │                              │
│                     │ • Update memory│                              │
│                     │ • Suggest      │                              │
│                     └─────────────────┘                              │
└─────────────────────────────────────────────────────────────────────┘
```

**Implementation:**

```typescript
class CognitiveLoop {
  private observeInterval = 1000;  // 1 second
  private reflectInterval = 60000; // 1 minute
  
  async start(): Promise<void> {
    // Continuous observation (lightweight)
    this.schedule(this.observe.bind(this), this.observeInterval);
    
    // Periodic reflection (heavier)
    this.schedule(this.reflect.bind(this), this.reflectInterval);
  }
  
  async observe(): Promise<void> {
    // Lightweight: just capture current state
    const state = await this.captureState();
    
    // Check for anomalies
    if (this.isAnomalous(state)) {
      await this.handleAnomaly(state);
    }
    
    // Check for opportunities
    if (this.isOpportunity(state)) {
      await this.handleOpportunity(state);
    }
  }
  
  async reflect(): Promise<void> {
    // Analyze recent episodes
    const recent = await this.memory.getRecentEpisodes(100);
    
    // Identify patterns
    const patterns = this.patternAnalyzer.find(recent);
    
    // Update learned knowledge
    for (const pattern of patterns) {
      await this.memory.updateKnowledge(pattern);
    }
    
    // Check for self-improvement opportunities
    const improvements = await this.identifyImprovements(recent);
    for (const improvement of improvements) {
      await this.executeImprovement(improvement);
    }
  }
}
```

### 5.2 Proactive Assistance: Anticipating User Needs

**Problem:** Users shouldn't have to ask — OSCAR should anticipate.

**Solution:** **Contextual Suggestion Engine**:

```typescript
class ProactiveAssistant {
  // Based on current context + history, predict what user needs
  async suggest(context: Context): Promise<Suggestion[]> {
    const suggestions: Suggestion[] = [];
    
    // 1. Pattern-based suggestions ("You usually do X at this time")
    const habitual = await this.findHabitualActions(context);
    suggestions.push(...habitual);
    
    // 2. Context-based suggestions ("Users typically Y when Z")
    const contextual = await this.findContextualSuggestions(context);
    suggestions.push(...contextual);
    
    // 3. Error-prevention suggestions ("You might want to save before...")
    const preventive = await this.findPreventiveSuggestions(context);
    suggestions.push(...preventive);
    
    // Rank and filter by confidence
    return suggestions
      .filter(s => s.confidence > 0.7)
      .sort((a, b) => b.confidence - a.confidence)
      .slice(0, 3);  // Top 3 only
  }
}
```

**Suggestion Types:**

| Type | Trigger | Example |
|------|---------|---------|
| **Habitual** | Time/place pattern | "Good morning! Ready for your daily email review?" |
| **Contextual** | App + task pattern | "Would you like me to save this file too?" |
| **Preventive** | Risky action detected | "This will delete 100 files. Create backup first?" |
| **Proactive** | Scheduled task due | "Quarterly report is due tomorrow. Start it now?" |
| **Learning** | New capability discovered | "I can now remove backgrounds. Try it on this photo?" |

### 5.3 Self-Supervised Learning: Improving from Failures

**Problem:** How does OSCAR learn from its mistakes without human labeling?

**Solution:** **Automatic Failure Analysis + Knowledge Update**:

```
┌─────────────────────────────────────────────────────────────────────┐
│                    SELF-SUPERVISED LEARNING                          │
│                                                                     │
│  FAILURE DETECTED:                                                  │
│                                                                     │
│  Action: Click "Export" button                                      │
│  Expected: File save dialog opens                                   │
│  Actual: Nothing happened                                           │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  AUTO-ANALYSIS (No Human Labels)                             │  │
│  │                                                               │  │
│  │  1. CHECK: Did the element exist? → YES                     │  │
│  │  2. CHECK: Was the element clickable? → NO (disabled)       │  │
│  │  3. CHECK: Why was it disabled? → Parent dialog not valid   │  │
│  │  4. HYPOTHESIS: Export requires valid document first        │  │
│  │  5. VERIFY: Try "Save" instead → SUCCESS                    │  │
│  │                                                               │  │
│  │  LEARNED:                                                     │  │
│  │  "Export button disabled when no valid document"            │  │
│  │  "Use 'Save As' as alternative when Export disabled"        │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                              │                                     │
│                              ▼                                     │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  KNOWLEDGE UPDATE                                             │  │
│  │                                                               │  │
│  │  Affordance {                                                 │  │
│  │    action: "export",                                         │  │
│  │    precondition: "valid_document",                           │  │
│  │    alternative: "save_as",                                   │  │
│  │    learned_from: "failure:export_disabled"                  │  │
│  │  }                                                           │  │
│  └──────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

**Learning Algorithm:**

```typescript
class SelfSupervisedLearner {
  async learnFromFailure(
    action: Action,
    expected: Outcome,
    actual: Outcome
  ): Promise<void> {
    // 1. Diagnose: Why did it fail?
    const diagnosis = await this.diagnose(action, expected, actual);
    
    // 2. If we can self-verify the hypothesis, do it
    if (diagnosis.canSelfVerify) {
      const verified = await this.verifyHypothesis(diagnosis);
      if (verified) {
        // 3. Update knowledge with verified insight
        await this.updateAffordanceKnowledge(diagnosis);
      }
    }
    
    // 4. Always: Update success rates
    await this.updateActionStats(action, success: false);
    
    // 5. If alternative worked, record it
    if (diagnosis.alternativeWorked) {
      await this.recordAlternative(action, diagnosis.alternative);
    }
  }
}
```

### 5.4 Anomaly Detection for Safety

**Problem:** OSCAR must detect when something is wrong before it causes harm.

**Solution:** **Multi-Layer Anomaly Detection**:

```typescript
class AnomalyDetector {
  // Layer 1: Rule-based (fast, catches known dangers)
  private rules: AnomalyRule[] = [
    {
      name: "delete_many_files",
      check: (action) => action.type === "delete" && action.targetCount > 10,
      severity: "critical",
      message: "Attempting to delete many files"
    },
    {
      name: "unusual_app",
      check: (action) => !this.isKnownApp(action.app),
      severity: "warning",
      message: "Action on unknown application"
    },
  ];
  
  // Layer 2: Sequence anomaly (catches unusual patterns)
  private sequenceDetector: SequenceAnomalyDetector;
  
  // Layer 3: Visual anomaly (catches unexpected screen states)
  private visualDetector: VisualAnomalyDetector;
  
  async detect(context: Context, action: Action): Promise<AnomalyAlert[]> {
    const alerts: AnomalyAlert[] = [];
    
    // Check rules
    for (const rule of this.rules) {
      if (rule.check(action)) {
        alerts.push({ severity: rule.severity, message: rule.message, rule: rule.name });
      }
    }
    
    // Check sequence
    const sequenceScore = await this.sequenceDetector.score(context.recentActions);
    if (sequenceScore > THRESHOLD) {
      alerts.push({ severity: "warning", message: "Unusual action sequence", score: sequenceScore });
    }
    
    // Check visual
    const visualAnomaly = await this.visualDetector.detect(context.screenGraph);
    if (visualAnomaly) {
      alerts.push({ severity: "warning", message: "Unexpected screen state", details: visualAnomaly });
    }
    
    return alerts;
  }
}
```

### 5.5 Collaborative Multi-OSCAR

**Problem:** Complex tasks benefit from multiple specialized agents.

**Solution:** **OSCAR Parliament for Complex Tasks**:

```
┌─────────────────────────────────────────────────────────────────────┐
│                    MULTI-OSCAR COLLABORATION                         │
│                                                                     │
│  TASK: "Prepare quarterly report from sales data"                   │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  OSCAR PARLIAMENT                                             │  │
│  │                                                               │  │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐         │  │
│  │  │ Noctua      │  │ Archimedes  │  │ Minerva     │         │  │
│  │  │ (Lead)      │  │ (Technical) │  │ (Reviewer)  │         │  │
│  │  │             │  │             │  │             │         │  │
│  │  │ Coordinates │  │ Extracts   │  │ Verifies    │         │  │
│  │  │ the plan    │  │ data from   │  │ data        │         │  │
│  │  │             │  │ Excel       │  │ accuracy    │         │  │
│  │  └─────────────┘  └─────────────┘  └─────────────┘         │  │
│  │         │                │                │                │  │
│  │         │    ┌────────────┴────────────┐   │                │  │
│  │         │    │      SHARED KNOWLEDGE    │   │                │  │
│  │         │    │      PELLET STORE        │   │                │  │
│  │         │    │                          │   │                │  │
│  │         │    │  • Extracted data facts  │   │                │  │
│  │         │    │  • Verification results  │   │                │  │
│  │         │    │  • Draft sections        │   │                │  │
│  │         │    └────────────┬────────────┘   │                │  │
│  │         │                 │                │                │  │
│  │         ▼                 ▼                ▼                │  │
│  │  ┌─────────────────────────────────────────────────────────┐  │  │
│  │  │  POWERPOINT OWL                                         │  │  │
│  │  │                                                          │  │  │
│  │  │  Assembles verified data into presentation              │  │  │
│  │  │  Following company template + style guide               │  │  │
│  │  └─────────────────────────────────────────────────────────┘  │  │
│  │                              │                                 │  │
│  │                              ▼                                 │  │
│  │  ┌─────────────────────────────────────────────────────────┐  │  │
│  │  │  EMAIL OWL                                               │  │  │
│  │  │                                                          │  │  │
│  │  │  Sends draft to manager for review                      │  │  │
│  │  │  Schedules follow-up if no response                     │  │  │
│  │  └─────────────────────────────────────────────────────────┘  │  │
│  └──────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

### 5.6 Deliverables

| Component | Architecture | Magic |
|-----------|-------------|-------|
| `oscar/cognition/loop.ts` | Continuous observe-reflect-act cycle | Time-scheduled processing |
| `oscar/cognition/proactive.ts` | Contextual suggestion engine | Pattern + contextual ranking |
| `oscar/cognition/self-learner.ts` | Self-supervised failure analysis | Automatic hypothesis testing |
| `oscar/cognition/anomaly-detector.ts` | Multi-layer anomaly detection | Rule + sequence + visual |
| `oscar/cognition/multi-oscar.ts` | Parliament orchestration | Shared pellet store + coordination |
| `oscar/cognition/voice.ts` | Voice command processing | Whisper + intent parsing |

### 5.7 Success Criteria

- Cognitive loop latency: <100ms for observation, <5s for reflection
- Proactive suggestion accuracy: >70% helpful (user accepts)
- Self-learning accuracy: >80% of learned insights verified correct
- Anomaly detection: >95% of harmful actions caught before execution
- Multi-OSCAR coordination: <1s overhead for task delegation

---

## Phase Integration: The Complete System

### How Phases Compose

```
┌─────────────────────────────────────────────────────────────────────┐
│                    OSCAR COMPLETE ARCHITECTURE                       │
│                                                                     │
│  USER ──────┐                                                        │
│  INPUT      │                                                        │
└──────┬──────┘                                                        │
       │                                                                │
       ▼                                                                │
┌──────────────────────────────────────────────────────────────────────┐
│ PHASE 3: INTENT PROCESSING ENGINE                                    │
│                                                                      │
│ Intent Parser → Semantic Action Resolver → DAG Builder → Validator   │
│                                                                      │
│ Output: Executable plan with verification conditions                 │
└──────────────────────────────────────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────────────────────────────────┐
│ PHASE 1: UNIVERSAL CONTROL INTERFACE                                │
│                                                                      │
│ Execute Action → Platform Adapter → Canonical Action → Verify        │
│                                                                      │
│ Uses: Screen Graph from Phase 2 for element targeting                 │
└──────────────────────────────────────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────────────────────────────────┐
│ PHASE 2: SCREEN GRAPH OBSERVATORY                                   │
│                                                                      │
│ Capture Screen → Perceive (OCR+YOLO+AX) → Fuse → Build Graph          │
│                                                                      │
│ Provides: Structured representation for intent + verification         │
└──────────────────────────────────────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────────────────────────────────┐
│ PHASE 4: VISUAL MEMORY NETWORK                                       │
│                                                                      │
│ Learn Affordances → Transfer Knowledge → Compose Skills → Retrieve    │
│                                                                      │
│ Stores: Episodes, Affordances, Skills                                │
│ Used by: Intent resolver (for learned actions), Proactive (patterns) │
└──────────────────────────────────────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────────────────────────────────┐
│ PHASE 5: AUTONOMOUS COGNITIVE AGENT                                  │
│                                                                      │
│ Observe → Reflect → Decide → Act → Learn (continuous)                │
│                                                                      │
│ Oversees: All phases, triggers improvements, handles anomalies      │
└──────────────────────────────────────────────────────────────────────┘
```

---

## Timeline and Milestones

| Phase | Duration | Key Milestone |
|-------|----------|---------------|
| Phase 1 | 4 weeks | Cross-platform control works |
| Phase 2 | 6 weeks | Screen Graph achieves >90% accuracy |
| Phase 3 | 8 weeks | Intent decomposition works reliably |
| Phase 4 | 8 weeks | Transfer learning demonstrates value |
| Phase 5 | 6 weeks | Proactive suggestions are helpful |

**Total: 32 weeks** for full implementation (can be parallelized with 5 engineers)

---

## Research Publications Potential

As we build OSCAR, we generate novel research in:

1. **"Universal UI Control through Semantic Action Spaces"** — How to map any app action to app-agnostic semantics
2. **"Self-Supervised Affordance Learning from Screen Observations"** — Learning UI affordances without human labels
3. **"Cross-Application Transfer Learning for UI Automation"** — Bootstrapping from known apps to new ones
4. **"Recovery-Oriented Execution for GUI Automation"** — Hierarchical recovery strategies
5. **"Proactive AI Assistants: Anticipation vs. Reaction"** — When to suggest vs. wait

---

## Conclusion

OSCAR's architectural magic lies not in any single innovation, but in the **composition of systems** that together achieve true computer agency:

1. **UCI** abstracts platform differences into a universal control bus
2. **SGO** transforms pixels into structured, queryable knowledge
3. **IPE** bridges human intent to executable plans
4. **VMN** enables learning, memory, and transfer
5. **ACA** provides continuous self-improvement and proactivity

Each phase is valuable alone, but together they form an agent that can **see, understand, act, learn, and anticipate** — a true digital colleague.
