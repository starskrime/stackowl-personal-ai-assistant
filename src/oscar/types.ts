export interface BoundingBox {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface Point {
  x: number;
  y: number;
}

export type ActionType = "click" | "type" | "drag" | "scroll" | "invoke" | "observe" | "hotkey" | "launch" | "close";

export type TargetSelector =
  | { type: "accessibility"; path: string }
  | { type: "semantic"; role?: string; label?: string; index?: number; app?: string }
  | { type: "visual"; region: BoundingBox }
  | { type: "coordinates"; x: number; y: number };

export interface CanonicalTarget {
  appBundleId?: string;
  windowTitle?: string;
  accessibilityPath?: string;
  visualRegion?: BoundingBox;
  semanticSelector?: {
    role?: string;
    label?: string;
    index?: number;
  };
}

export interface CanonicalAction {
  type: ActionType;
  target: CanonicalTarget;
  params: Record<string, unknown>;
  timestamp: number;
  traceId: string;
}

export interface VerificationCondition {
  type: "element_exists" | "element_focused" | "state_changed" | "window_opened" | "window_closed" | "text_appeared" | "screenshot_match";
  target?: CanonicalTarget;
  expected?: Record<string, unknown>;
  timeout?: number;
}

export interface VerificationResult {
  success: boolean;
  attempts?: number;
  error?: string;
  delta?: AccessibilityDelta;
}

export interface AccessibilityState {
  focusedElement?: string;
  windows: WindowInfo[];
  elements: Map<string, AccessibilityElement>;
}

export interface AccessibilityElement {
  id: string;
  path: string;
  role: string;
  label: string;
  value?: string;
  description?: string;
  bounds: BoundingBox;
  state: Record<string, boolean>;
  children: string[];
  parent?: string;
}

export interface WindowInfo {
  id: string;
  title: string;
  bundleId: string;
  bounds: BoundingBox;
  isFocused: boolean;
}

export interface AccessibilityDelta {
  type: "element_focused" | "element_added" | "element_removed" | "attribute_changed" | "window_opened" | "window_closed" | "text_changed";
  elementId?: string;
  elementLabel?: string;
  attributeName?: string;
  oldValue?: unknown;
  newValue?: unknown;
}

export interface ScreenBuffer {
  id: number;
  imageData: Buffer;
  width: number;
  height: number;
  timestamp: number;
  bounds?: BoundingBox;
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

export interface ScreenGraph {
  id: string;
  timestamp: number;
  resolution: { width: number; height: number };
  elements: Map<string, UIElement>;
  regions: Region[];
  focus: {
    app: string;
    element: string | null;
    cursor: Point;
  };
}

export interface Region {
  id: string;
  type: "toolbar" | "sidebar" | "content" | "dialog" | "menu" | "statusbar" | "unknown";
  bounds: BoundingBox;
  elements: string[];
}

export interface ExecutionPlan {
  id: string;
  steps: ExecutionStep[];
  currentStep: number;
  status: "planned" | "running" | "paused" | "completed" | "failed";
  startedAt?: number;
  completedAt?: number;
}

export interface ExecutionStep {
  id: string;
  action: CanonicalAction;
  verification?: VerificationCondition;
  dependsOn: string[];
  status: "pending" | "running" | "completed" | "failed" | "skipped";
  result?: { success: boolean; error?: string };
  attempts: number;
}

export interface Checkpoint {
  id: string;
  planId: string;
  stepIndex: number;
  timestamp: number;
  screenGraph: ScreenGraph;
  appStates: Map<string, AppState>;
  memoryState: unknown;
  diffs: StateDiff[];
}

export interface AppState {
  bundleId: string;
  openDocuments: string[];
  modified: boolean;
  cursorPosition: Point;
  panelStates: Record<string, boolean>;
}

export interface StateDiff {
  type: "added" | "removed" | "modified";
  path: string[];
  oldValue?: unknown;
  newValue?: unknown;
}

export interface Affordance {
  id: string;
  elementPattern: VisualSignature;
  action: string;
  targetRole: string;
  targetLabel?: string;
  app: string;
  successRate: number;
  attempts: number;
  lastAttempt?: number;
  alternatives: string[];
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
    region: string;
    alignment: "left" | "center" | "right";
    relativeY: number;
  };
}

export interface Skill {
  id: string;
  name: string;
  description: string;
  parameters: SkillParameter[];
  steps: SkillStep[];
  prerequisites?: string[];
  successConditions: VerificationCondition[];
}

export interface SkillParameter {
  name: string;
  type: "string" | "number" | "boolean" | "path" | "selection";
  description?: string;
  required: boolean;
  defaultValue?: unknown;
}

export interface SkillStep {
  action: string;
  target?: TargetSelector;
  parameters?: Record<string, unknown>;
  verification?: VerificationCondition;
}

export interface RecoveryStrategy {
  name: string;
  applicability: (failure: FailureContext) => number;
  execute: (failure: FailureContext, state: ExecutionState) => Promise<RecoveryResult>;
}

export interface FailureContext {
  type: "element_not_found" | "action_failed" | "unexpected_dialog" | "timeout" | "app_crashed" | "permission_denied" | "unknown_error";
  action: CanonicalAction;
  target?: CanonicalTarget;
  error?: string;
  alternatives?: string[];
  dialog?: {
    title?: string;
    message?: string;
    buttons: string[];
  };
}

export interface RecoveryResult {
  success: boolean;
  replacement?: CanonicalAction;
  dialogResponse?: string;
  restarted?: boolean;
  requiresUser?: boolean;
  message?: string;
  options?: string[];
}

export interface ExecutionState {
  plan: ExecutionPlan;
  screenGraph: ScreenGraph;
  activeApps: AppInfo[];
  memory: MemoryStore;
  checkpoint?: Checkpoint;
  resolver: SASResolver;
}

export interface AppInfo {
  bundleId: string;
  name: string;
  version?: string;
  pid: number;
}

export interface MemoryStore {
  episodic: Episode[];
  semantic: Affordance[];
  procedural: Skill[];
}

export interface Episode {
  id: string;
  timestamp: number;
  app: string;
  actions: CanonicalAction[];
  outcome: "success" | "partial" | "failed";
  userFeedback?: "accepted" | "corrected" | "rejected";
}

export interface SASResolver {
  resolve(action: string, context: ScreenGraph, app: AppInfo): ResolvedStep[];
}

export interface ResolvedStep {
  action: CanonicalAction;
  confidence: number;
  alternatives: CanonicalAction[];
}
