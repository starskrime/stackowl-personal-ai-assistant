# OSCAR: Omniscient Screen Control and Automation Runtime

## 1. Vision Statement

OSCAR transforms StackOwl into an **omniscient computer agent** — a system that can perceive, reason about, and act upon any application with human-like flexibility but machine precision. Unlike narrow RPA tools or browser-only automation, OSCAR operates as a **universal application controller** that:

1. **Sees** the entire screen as a multimodal context
2. **Understands** application semantics through visual + accessibility + behavioral analysis
3. **Acts** through a unified action space (click, type, drag, navigate, invoke)
4. **Learns** from each interaction to improve reliability
5. **Recovers** autonomously when unexpected states arise
6. **Explains** its actions and asks for clarification when uncertain

The agent should handle tasks like:
- "Help me clear the background in Photoshop" → launches PS, identifies tools, executes sequence
- "My Word document won't open, debug it" → launches Word, diagnoses issue, reports/suggests fix
- "Do a regression test on our Facebook integration" → orchestrates multi-step test scenario
- "File my taxes in TurboTax" → navigates complex multi-window wizard
- "Help me fix the formatting in this legacy Excel sheet" → analyzes VBA, repairs formulas

---

## 2. Research Foundations

### 2.1 Why Current Approaches Are Insufficient

| Approach | Limitation |
|----------|------------|
| **RPA (UiPath)** | Brittle selectors, per-application coding, no generalization |
| **Anthropic Computer Use** | Browser-only, DOM-centric, fragile to visual changes |
| **OpenAI Operator** | Same — browser sandbox, no desktop app support |
| **Apple Accessibility API** | Mac-only, requires app cooperation, limited visual understanding |
| **Windows UIA** | Windows-only, inconsistent API quality, poor image understanding |

### 2.2 Key Research Gaps

1. **Visual Grounding at Scale** — Connecting pixels to semantic elements without relying on fragile selectors
2. **Cross-Application Generalization** — One model that works across Photoshop, Word, Chrome, and 10,000 other apps
3. **Behavioral Recovery** — Autonomous recovery from unexpected dialogs, timeouts, state changes
4. **Efficient Multimodal Context** — Representing screen state without drowning in pixels
5. **Application State Modeling** — Building/updating mental models of complex app state

### 2.3 Proposed Innovations

| Innovation | Description |
|------------|-------------|
| **Semantic Action Space (SAS)** | Hierarchical actions (e.g., `edit.crop.removeBackground`) instead of raw coordinates |
| **Visual Memory Graph** | Persistent representation of UI elements with learned affordances |
| **Cross-Application Transfer Learning** | Knowledge of "buttons" and "dialogs" transfers across apps |
| **Probabilistic Execution with Verification** | Attempt action → verify result → rollback if wrong |
| **Intent Decomposition Engine** | Break "fix my document" into executable micro-steps |
| **Self-Supervised UI Learning** | Learn app behavior without expensive human labeling |

---

## 3. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                         USER REQUEST                                 │
│   "Help me clear the background in Photoshop"                       │
└─────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     INTENT DECOMPOSITION ENGINE                     │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────────────────┐   │
│  │ Task Graph  │  │ App         │  │ Execution Plan             │   │
│  │ Builder     │→ │ Detector    │→ │ Generator                  │   │
│  └─────────────┘  └──────────────┘  └────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
                                   │
                    ┌──────────────┼──────────────┐
                    ▼              ▼              ▼
        ┌───────────────────┐ ┌──────────┐ ┌─────────────────┐
        │ SEMANTIC ACTION   │ │ INDIRECT │ │ DIRECT ACCESS    │
        │ SPACE (SAS)       │ │ CONTROL  │ │ (Accessibility)  │
        │                   │ │          │ │                  │
        │ • High-level      │ │ • OCR    │ │ • macOS AX       │
        │   verbs           │ │ • Visual │ │ • Windows UIA    │
        │ • App-agnostic    │ │   match  │ │ • X11/EWMH      │
        │ • Self-verifying  │ │ • Screenshot│                  │
        └───────────────────┘ └──────────┘ └─────────────────┘
                    │              │              │
                    └──────────────┼──────────────┘
                                   ▼
        ┌─────────────────────────────────────────────────────────────┐
        │                 EXECUTION & VERIFICATION LOOP                │
        │                                                               │
        │   Act → Observe → Verify → (Recovery)? → Act → ...          │
        │                                                               │
        └─────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
        ┌─────────────────────────────────────────────────────────────┐
        │                     VISUAL MEMORY GRAPH                      │
        │   Persistent learned representation of UI elements          │
        └─────────────────────────────────────────────────────────────┘
```

---

## 4. Core Components

### 4.1 Intent Decomposition Engine (IDE)

**Purpose:** Transform vague human requests into executable task graphs.

**Input:** Natural language request + current screen context
**Output:** Directed Acyclic Graph (DAG) of semantic actions

**Algorithm:**

```
1. PARSE request into verb phrases and object phrases
   "clear the background" → verb="clear", object="background"
   
2. MATCH verbs to Semantic Action Space taxonomy
   "clear" → SAS.edit.clear, SAS.filter.remove, SAS.layer.delete
   
3. IDENTIFY target application via:
   - Explicit mention: "in Photoshop"
   - Context window matching: currently focused app
   - Object analysis: ".psd" file → launch Photoshop
   
4. GENERATE execution DAG with dependencies:
   Node: { action: SAS.layer.select, params: { target: "background" }, verify: "layer highlighted" }
   Node: { action: SAS.edit.crop, params: { mode: "transparent" }, dependsOn: [layer_select] }
   
5. VALIDATE DAG:
   - Check tool availability in target app
   - Verify action sequence is valid (no circular deps)
   - Estimate success probability
```

**Self-Verification Checkpoints:**
Each node includes a verification condition that determines if the action succeeded:
- Screen region matches expected pattern
- Application state change detected
- File/modified timestamp updated
- Accessibility tree contains expected element

### 4.2 Semantic Action Space (SAS)

**Philosophy:** Actions should be *intentional* — describe *what* to do, not *where* to click.

#### 4.2.1 Taxonomy Hierarchy

```
SAS
├── navigation
│   ├── open(application)
│   ├── close(target)
│   ├── switch_to(application)
│   ├── minimize()
│   ├── maximize()
│   └── focus(element)
├── input
│   ├── type(text)
│   ├── paste(content)
│   ├── upload(file)
│   └── drag(from, to)
├── edit
│   ├── select(target, mode)
│   ├── clear(target)
│   ├── cut()
│   ├── copy()
│   ├── paste()
│   ├── delete(target)
│   ├── undo()
│   ├── redo()
│   ├── find(text)
│   ├── replace(find, replace)
│   └── format(target, properties)
├── dialog
│   ├── confirm()
│   ├── cancel()
│   ├── submit()
│   ├── choose(option)
│   └── alert_respond(response)
├── file
│   ├── save()
│   ├── save_as(path)
│   ├── open(path)
│   ├── export(format)
│   ├── import(source)
│   └── print(settings)
├── view
│   ├── zoom(level)
│   ├── scroll(direction, amount)
│   ├── toggle_panel(panel)
│   └── refresh()
└── application_specific
    └── {app}:{action}  // Extensible per-app vocabulary
```

#### 4.2.2 Action Resolution

Actions resolve to concrete UI operations through **grounding**:

```
SAS.edit.clear(target: "background layer")
    │
    ├─→ [Photoshop] resolve to:
    │       1. Click "Layers" panel
    │       2. Select "Background" layer
    │       3. Menu → Layer → Delete Layer
    │       4. Or: Click "Delete" via direct UI element
    │
    ├─→ [GIMP] resolve to:
    │       1. Right-click layer → Delete
    │
    └─→ [Unknown App] resolve via:
            1. Visual matching of "clear" button/icon
            2. Accessibility tree search for "delete"
            3. Keyboard shortcut inference (Delete key)
```

### 4.3 Multimodal Screen Perception (MSP)

**Purpose:** Convert screen into structured, queryable representation.

#### 4.3.1 Perception Pipeline

```
┌──────────────┐    ┌──────────────┐    ┌──────────────────────────────┐
│  Screenshot  │───→│  OCR + Icon  │───→│  Accessibility Tree Overlay  │
│  Capture     │    │  Detection   │    │                              │
└──────────────┘    └──────────────┘    └──────────────────────────────┘
                           │                        │
                           ▼                        ▼
              ┌─────────────────────┐   ┌────────────────────────────┐
              │  Visual Element     │   │  Semantic Region           │
              │  Detection (YOLO)    │   │  Classification             │
              │                     │   │                             │
              │  • Buttons          │   │  • Toolbar                  │
              │  • Input fields     │   │  • Sidebar                  │
              │  • Menus            │   │  • Content area             │
              │  • Panels           │   │  • Dialog                   │
              └─────────────────────┘   └────────────────────────────┘
                           │                        │
                           └──────────┬─────────────┘
                                      ▼
                          ┌─────────────────────────┐
                          │  Unified Screen Graph  │
                          │                         │
                          │  Nodes: UI elements     │
                          │  Edges: spatial/temporal│
                          │  Attributes: labels,   │
                          │          states, affordances│
                          └─────────────────────────┘
```

#### 4.3.2 Screen Graph Schema

```typescript
interface ScreenGraph {
  id: string;
  timestamp: number;
  resolution: { width: number; height: number };
  
  elements: Map<string, UIElement>;
  relationships: Array<{
    from: string;
    to: string;
    type: "contains" | "siblings" | "overlaps" | "z-order";
  }>;
  
  regions: Array<{
    id: string;
    type: "toolbar" | "sidebar" | "content" | "dialog" | "menu" | "unknown";
    bounds: BoundingBox;
    elements: string[];
  }>;
  
  focus: {
    app: string;
    element: string | null;
    cursor: { x: number; y: number };
  };
}

interface UIElement {
  id: string;
  type: "button" | "input" | "menu" | "panel" | "dialog" | "icon" | "text" | "image" | "unknown";
  
  bounds: BoundingBox;
  visual: {
    screenshot_region: BoundingBox;
    icon_hash?: string;
    text_ocr?: string;
    style?: { bg_color: string; text_color: string; font_size?: number; };
  };
  
  semantic: {
    label?: string;           // "OK", "Cancel", "Save"
    role?: string;            // Accessibility role
    state?: Record<string, boolean>;  // { disabled: false, focused: true }
    description?: string;
    keyboard_shortcut?: string;
  };
  
  affordances: {
    clickable: boolean;
    editable: boolean;
    scrollable: boolean;
    draggable: boolean;
    keyboard_focusable: boolean;
  };
  
  history: Array<{
    timestamp: number;
    action: "clicked" | "typed" | "hovered" | "focused";
    success: boolean;
  }>;
}
```

### 4.4 Visual Memory Graph (VMG)

**Purpose:** Persistent learned representation that improves over time.

#### 4.4.1 Memory Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    VISUAL MEMORY GRAPH                           │
│                                                                 │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────────────┐ │
│  │ App:        │    │ Element:     │    │ Pattern:           │ │
│  │ Photoshop   │───→│ Magic Eraser │───→│ Tool located in     │ │
│  │             │    │ Tool         │    │ upper-left toolbar  │ │
│  └─────────────┘    └─────────────┘    └─────────────────────┘ │
│         │                  │                     │              │
│         │    ┌──────────────┘                     │              │
│         ▼    ▼                                    ▼              │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │ AFFORDANCE LEARNINGS                                        ││
│  │ "Magic Eraser tool can clear backgrounds"                   ││
│  │ "Used 47 times, 94% success rate"                           ││
│  │ "Last failed: 3 weeks ago, app updated"                     ││
│  └─────────────────────────────────────────────────────────────┘│
│                                                                 │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │ SPATIAL MEMORY                                             ││
│  │ "Toolbar always in upper 15% of screen"                     ││
│  │ "File menu is leftmost menu item"                           ││
│  │ "OK/Cancel buttons always bottom-right in dialogs"          ││
│  └─────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────┘
```

#### 4.4.2 Learning Mechanisms

**Supervised Learning:**
- Human demonstrations: "Click here" → learns association
- Corrections: "No, that's not it" → learns disambiguation

**Self-Supervised:**
- Attempted actions + outcomes: if click worked, reinforce pattern
- Similar screenshot regions across apps: "close buttons look similar"
- State change detection: clicking X closes dialogs universally

**Transfer Learning:**
- Desktop app UI → learned affordances transfer to similar apps
- Web browser → learned patterns transfer to web-based desktop apps
- One app's toolbar → structure generalizes to other toolbars

### 4.5 Execution Controller with Recovery

**Purpose:** Execute action DAG with autonomous recovery.

#### 4.5.1 Execution States

```
┌─────────┐    ┌──────────┐    ┌────────────┐    ┌──────────┐
│ PLANNED │───→│ ACTIVE   │───→│ VERIFYING   │───→│ COMPLETE  │
└─────────┘    └──────────┘    └────────────┘    └──────────┘
                     │                │
                     │                ▼
                     │         ┌────────────┐
                     │         │  RECOVERING │
                     │         └────────────┘
                     │                │
                     ▼                ▼
              ┌────────────┐   ┌────────────┐
              │   FAILED   │   │   RETRYING  │
              └────────────┘   └────────────┘
```

#### 4.5.2 Recovery Strategies

| Failure Type | Detection | Recovery Strategy |
|--------------|-----------|-------------------|
| **Wrong element clicked** | Verification failed | Re-locate target, try again |
| **Unexpected dialog** | New window/dialog detected | Parse dialog, respond appropriately |
| **Element moved/renamed** | Selector not found | Re-scan accessibility tree, find by semantic |
| **App crashed** | Process terminated | Restart app, restore state from checkpoint |
| **Timeout** | Action not completed in SLA | Offer alternatives, ask user |
| **Permission denied** | OS permission error | Request permission, explain why |
| **App updated UI** | Known pattern not found | Screenshot comparison, adapt |

#### 4.5.3 Checkpoint & Rollback

```typescript
interface Checkpoint {
  id: string;
  action_index: number;
  timestamp: number;
  screen_graph: ScreenGraph;
  app_state: {
    open_documents: string[];
    modified: boolean;
    cursor_position: { x: number; y: number };
    panel_states: Record<string, boolean>;
  };
  memory_state: SerializedMemory;
}

// On failure:
// 1. Restore app state from checkpoint
// 2. Clear any partial modifications
// 3. Optionally retry with different approach
```

### 4.6 Cross-Application Orchestration

**Purpose:** Coordinate actions across multiple applications.

#### 4.6.1 Workflow Schema

```yaml
workflow:
  name: "Photoshop Background Removal"
  description: "Remove background from product photo"
  
  steps:
    - id: launch_photoshop
      action: launch(application: "Adobe Photoshop 2024")
      verify: app_running(window_title: contains("Adobe Photoshop"))
      
    - id: open_file
      action: file.open(path: "{{input_file}}")
      verify: layer_palette.visible()
      
    - id: select_magic_eraser
      action: click(tool: "Magic Eraser Tool")
      verify: tool_active(icon: "magic_eraser")
      
    - id: set_tolerance
      action: type(tolerance: "32")  # via properties panel
      verify: tolerance_value(32)
      
    - id: click_background
      action: click(region: "background_pixels")
      verify: selection_exists()
      
    - id: delete_selection
      action: keyboard(key: "Delete")
      verify: layer.transparency(100%)
      
    - id: export_png
      action: file.export(format: "PNG", transparent: true)
      verify: file.exists(output_path)
```

#### 4.6.2 Application Switching

When a workflow spans apps (e.g., "Extract data from Excel, paste into PowerPoint"):

```
┌─────────────────┐
│ Excel Action    │
│ [Copy range]    │
└────────┬────────┘
         │
         ▼ (clipboard.set_data())
┌─────────────────┐
│ PowerPoint      │
│ [Paste + Format]│
└─────────────────┘
```

---

## 5. Platform-Specific Implementations

### 5.1 macOS Implementation

**Primary:** Accessibility API (AXUIElement)
**Secondary:** CGEvent for keyboard/mouse, CGWindowList for screen

```
┌─────────────────────────────────────────────────────────────┐
│ macOS CONTROL LAYER                                         │
│                                                             │
│  ┌───────────────┐  ┌───────────────┐  ┌────────────────┐   │
│  │ AXUIElement   │  │ CGEvent       │  │ CGWindowList   │   │
│  │ (Accessibility│  │ (InputEvents) │  │ (ScreenCapture│   │
│  │  + Actions)   │  │               │  │  + Windows)    │   │
│  └───────────────┘  └───────────────┘  └────────────────┘   │
│           │                │                   │            │
│           └────────────────┼───────────────────┘            │
│                            ▼                                 │
│                   ┌─────────────────┐                       │
│                   │ Screen Graph    │                       │
│                   │ Builder         │                       │
│                   └─────────────────┘                       │
└─────────────────────────────────────────────────────────────┘
```

**Capabilities:**
- Read/write all accessibility elements
- Perform AXActions (press, show menu, etc.)
- Inject keyboard/mouse events
- Capture screen regions
- Read process list, window hierarchy

**Permissions Required:**
- Accessibility (System Preferences → Privacy → Accessibility)
- Screen Recording (for screenshot capture)

### 5.2 Windows Implementation

**Primary:** UI Automation (UIAutomationClient)
**Secondary:** SendInput/PostMessage for input, PrintWindow for capture

```
┌─────────────────────────────────────────────────────────────┐
│ WINDOWS CONTROL LAYER                                       │
│                                                             │
│  ┌───────────────┐  ┌───────────────┐  ┌────────────────┐ │
│  │ UIAutomation  │  │ SendInput/     │  │ PrintWindow/    │ │
│  │ (Find/Act on  │  │ PostMessage    │  │ BitBlt         │ │
│  │  elements)    │  │ (InputEvents)  │  │ (ScreenCapture) │ │
│  └───────────────┘  └───────────────┘  └────────────────┘ │
│           │                │                   │           │
│           └────────────────┼───────────────────┘           │
│                            ▼                                 │
│                   ┌─────────────────┐                       │
│                   │ Screen Graph    │                       │
│                   │ Builder         │                       │
│                   └─────────────────┘                       │
└─────────────────────────────────────────────────────────────┘
```

**Capabilities:**
- IUIAutomationElement traversal
- UIAutomation patterns (Invoke, Selection, etc.)
- COM-based event monitoring
- High-DPI aware coordinates
- Multiple monitor support

### 5.3 Linux Implementation

**Primary:** AT-SPI2 (via at-spi2-core + at-spi2-atk)
**Secondary:** X11/EWMH, libinput for input, XDamage for monitoring

```
┌─────────────────────────────────────────────────────────────┐
│ LINUX CONTROL LAYER                                         │
│                                                             │
│  ┌───────────────┐  ┌───────────────┐  ┌────────────────┐  │
│  │ AT-SPI2       │  │ X11/EvDev     │  │ X11 Screen     │  │
│  │ (Accessibility│  │ (InputEvents) │  │ Capture        │  │
│  │  + Actions)  │  │               │  │                │  │
│  └───────────────┘  └───────────────┘  └────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

**Wayland Support (Future):**
- KDE Plasma's KAccessible
- GNOME's AT-SPI2-over-D-Bus
- Custom protocol for screen capture

---

## 6. Advanced Capabilities

### 6.1 Visual Task Macros

Record and replay user demonstrations:

```
User: "Watch what I do and repeat it"
  1. User clicks Photoshop toolbar
  2. User selects Magic Eraser
  3. User clicks background
  4. User presses Delete
  5. User exports as PNG
  
OSCAR records:
  [
    { action: click, target: { type: toolbar_region, app: Photoshop } },
    { action: click, target: { type: icon, label: "Magic Eraser" } },
    { action: click, target: { type: canvas_region, predicate: "background pixels" } },
    { action: keypress, key: "Delete" },
    { action: navigate, path: "File > Export As > PNG" }
  ]
  
OSCAR generalizes:
  - "toolbar_region" → works across apps with toolbars
  - "background pixels" → learned to detect similar patterns
  - "File > Export As" sequence → learned as export pattern
```

### 6.2 Predictive Assistance

Based on current context, predict next action:

```
Context: User is in Excel, cell A1 selected, typing "=SUM("
Prediction: User will complete with B1:B10 or similar range
Suggestion: "Should I complete with B1:B10?"

Context: User is viewing image in Photoshop
Prediction: User might want to crop, resize, or adjust colors
Quick Actions: [Crop] [Resize] [Brightness/Contrast] [Remove Background]
```

### 6.3 Multimodal Reasoning

Combine visual + textual + structural reasoning:

```
User: "Why is my document printing blank pages?"

OSCAR analyzes:
1. VISUAL: Screenshot shows Print Preview with blank pages
2. STRUCTURAL: Document has 20 pages, all content in headers
3. HYPOTHESIS: Printer configured to print headers only
4. VERIFICATION: Check printer settings via Settings app
5. DIAGNOSIS: "Page Setup > Options > 'Different first page' enabled"
6. FIX: Disable option or clear "Different first page header"
```

### 6.4 Collaborative Automation

Multiple agents work on complex tasks:

```
Task: "Migrate our company's website content to the new CMS"

OSCAR Parliament:
├─ Noctua (Lead): Coordinates overall migration plan
├─ Archimedes (Technical): Writes migration scripts
├─ Minerva (QA): Verifies each page renders correctly
└─ Merlin (Data): Maps old content schema to new

Each owl operates in its own sandbox, communicates via shared
knowledge pellet store, syncs via parliament orchestration.
```

---

## 7. Security & Safety

### 7.1 Permission Model

```typescript
interface OSCARPermissions {
  // Controlled by user
  can_launch_apps: boolean;
  can_install_software: boolean;
  can_change_system_settings: boolean;
  can_access_files: PathPermission[];
  can_network: boolean;
  
  // Application-specific
  allowed_apps: string[];      // ["Adobe Photoshop*", "Microsoft Word*"]
  blocked_apps: string[];      // ["1Password*", "Password Wallet*"]
  
  // Action restrictions  
  allowed_actions: string[];   // ["edit.*", "file.save", "navigation.*"]
  blocked_actions: string[];   // ["file.delete", "system.format"]
  
  // Confirmation required for
  confirm_on: {
    launch_new_app: boolean;
    file_overwrite: boolean;
    network_request: boolean;
    system_setting_change: boolean;
    purchase_transaction: boolean;
  };
}

interface PathPermission {
  path: string;
  mode: "read" | "write" | "execute" | "full";
  recursive: boolean;
}
```

### 7.2 Sandbox Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    OSCAR SANDBOX                                │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │              Action Policy Engine                        │   │
│  │  • Whitelist checks before any action                    │   │
│  │  • Rate limiting per action type                         │   │
│  │  • Anomaly detection (unusual sequences)                │   │
│  └──────────────────────────────────────────────────────────┘   │
│                              │                                  │
│                              ▼                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │              Execution Environment                        │   │
│  │                                                           │   │
│  │   ┌─────────┐  ┌─────────┐  ┌─────────┐                  │   │
│  │   │ Action  │  │ Action  │  │ Action  │                  │   │
│  │   │ Worker  │  │ Worker  │  │ Worker  │                  │   │
│  │   │ (App 1) │  │ (App 2) │  │ (App N) │                  │   │
│  │   └─────────┘  └─────────┘  └─────────┘                  │   │
│  │                                                           │   │
│  └──────────────────────────────────────────────────────────┘   │
│                              │                                  │
│                              ▼                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │              Verification & Rollback                     │   │
│  │  • Every action verified                                  │   │
│  │  • Checkpoint before destructive actions                 │   │
│  │  • Automatic rollback on failure                         │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

### 7.3 Audit Trail

```typescript
interface AuditEntry {
  timestamp: number;
  action: string;
  target: { app: string; element?: string; path?: string };
  result: "success" | "failed" | "blocked" | "requires_confirmation";
  reason?: string;        // Why blocked/failed
  user_verified?: boolean; // User confirmed this action
}
```

---

## 8. Technical Specifications

### 8.1 Performance Targets

| Metric | Target | Measurement |
|--------|--------|-------------|
| Screen perception latency | <50ms | Screenshot → ScreenGraph |
| Action execution latency | <100ms | Command → UI response |
| Recovery time (minor failures) | <500ms | Detection → re-execution |
| Memory footprint | <200MB | Per active application |
| CPU usage (idle) | <2% | Background monitoring |
| Power impact | Minimal | Battery drain comparison |

### 8.2 Dependencies

**Core:**
- Screen capture: Platform native (CGWindowList, UIAutomation, AT-SPI2)
- OCR: Tesseract 5.x (local) or cloud fallback
- Visual detection: ONNX runtime with YOLO model
- Accessibility: Platform native APIs

**Optional enhancements:**
- Claude API for semantic reasoning
- Whisper for voice commands
- Video codec for screen recording

### 8.3 File Structure

```
src/
├── oscar/
│   ├── index.ts                    # Main entry
│   ├── perception/                 # Screen perception
│   │   ├── capture/                # Platform capture
│   │   │   ├── macos.ts
│   │   │   ├── windows.ts
│   │   │   └── linux.ts
│   │   ├── ocr.ts                  # Text recognition
│   │   ├── detection.ts            # Element detection
│   │   └── screen-graph.ts         # Graph construction
│   │
│   ├── memory/                     # Visual memory
│   │   ├── graph.ts                # VMG implementation
│   │   ├── learn.ts                # Learning mechanisms
│   │   └── transfer.ts             # Transfer learning
│   │
│   ├── action/                     # Action execution
│   │   ├── semantic/               # SAS implementation
│   │   │   ├── taxonomy.ts
│   │   │   ├── resolver.ts         # Action → UI mapping
│   │   │   └── verifiers.ts        # Result verification
│   │   ├── execution/              # Execution engine
│   │   │   ├── controller.ts
│   │   │   ├── recovery.ts
│   │   │   └── checkpoint.ts
│   │   └── platform/               # Platform adapters
│   │       ├── macos.ts            # AXUIElement + CGEvent
│   │       ├── windows.ts          # UIAutomation + SendInput
│   │       └── linux.ts            # AT-SPI2 + X11
│   │
│   ├── intent/                     # Intent decomposition
│   │   ├── parser.ts
│   │   ├── decomposer.ts
│   │   └── planner.ts
│   │
│   ├── cross-app/                  # Multi-app workflows
│   │   ├── orchestrator.ts
│   │   └── clipboard.ts
│   │
│   ├── security/                   # Safety & permissions
│   │   ├── policy.ts
│   │   ├── sandbox.ts
│   │   └── audit.ts
│   │
│   └── skills/                     # Learned macros
│       ├── recorder.ts
│       └── playback.ts
```

---

## 9. Implementation Phases

### Phase 1: Foundation (MVP)
- [ ] Cross-platform screen capture
- [ ] Basic OCR and element detection
- [ ] Accessibility API integration (macOS first)
- [ ] Core action space (click, type, navigate)
- [ ] Simple verification (element exists after action)
- [ ] Permission prompts

**Deliverable:** "Hello World" automation — launch app, click button, type text

### Phase 2: Perception
- [ ] Screen Graph construction
- [ ] YOLO element detection
- [ ] Visual affordance learning
- [ ] Cross-app pattern recognition
- [ ] Multimodal context compression

**Deliverable:** "Find the OK button" works without accessibility API

### Phase 3: Intelligence
- [ ] Intent Decomposition Engine
- [ ] Semantic Action Space resolver
- [ ] Recovery strategies
- [ ] Checkpoint/rollback
- [ ] Visual Memory Graph basics

**Deliverable:** "Open Word and fix the formatting in my document" works

### Phase 4: Mastery
- [ ] Full Visual Memory with transfer learning
- [ ] Predictive assistance
- [ ] Multi-agent orchestration
- [ ] Natural language task recording
- [ ] Complex cross-app workflows

**Deliverable:** "Help me prepare our quarterly report" — coordinates Excel, PowerPoint, email

### Phase 5: Autonomy
- [ ] Self-supervised learning from failures
- [ ] Proactive task suggestions
- [ ] Voice control integration
- [ ] Collaborative multi-OSCAR coordination
- [ ] Full sandbox with anomaly detection

**Deliverable:** OSCAR becomes a true digital colleague

---

## 10. Research Questions

As we build OSCAR, we must answer:

1. **Generalization:** How many apps must OSCAR see to learn UI patterns that generalize to new apps?
2. **Verification:** What's the minimum verification needed to ensure reliability without excessive slowdown?
3. **Recovery granularity:** Should recovery be at action level, task level, or app level?
4. **Memory architecture:** Centralized vs. distributed VMG — which scales better?
5. **Human-in-the-loop:** When should OSCAR ask for clarification vs. attempt recovery?
6. **Security boundary:** How to allow helpful automation while preventing misuse?
7. **Learning efficiency:** Can OSCAR learn a new app in <5 minutes of interaction?
8. **Failure modes:** What common patterns indicate an unrecoverable situation?

---

## 11. Success Metrics

| Metric | Definition | Target |
|--------|------------|--------|
| **Task Success Rate** | % tasks completed without user intervention | >90% |
| **Time to Automate** | Time from request to working automation | <2 min |
| **Recovery Rate** | % failures recovered autonomously | >80% |
| **Learning Speed** | Apps learned per week of operation | >10 |
| **User Satisfaction** | Likert scale on helpfulness | >4.5/5 |
| **False Positive Rate** | Accidentally wrong actions per 1000 | <5 |
| **Context Window Efficiency** | Token reduction ratio for screen context | >10x |

---

## 12. Example User Journeys

### Journey 1: Photoshop Background Removal

```
User: "Help me clear the background in this product photo"

OSCAR:
1. [Launches Photoshop, opens file]
2. [Perceives screen: toolbar, layers panel, canvas]
3. [Decomposes: select tool → click background → delete]
4. [Executes: clicks Magic Eraser in toolbar]
5. [Verifies: tool icon highlighted, cursor changed]
6. [Executes: clicks on background region]
7. [Verifies: marching ants selection visible]
8. [Executes: presses Delete]
9. [Verifies: pixels become transparent (checkerboard)]
10. [Offers: "Export as PNG with transparency?"]
```

### Journey 2: Word Debug

```
User: "My Word document won't open. Debug it."

OSCAR:
1. [Attempts to launch Word with file]
2. [Detects: Error dialog "File is corrupted or encrypted"]
3. [Perceives: Dialog with "OK" and "Help" buttons]
4. [Decomposes: investigate corruption level → assess recovery options]
5. [Executes: clicks "Help" to understand error]
6. [Verifies: Help dialog explains possible causes]
7. [Diagnoses: File may be truncated or in wrong format]
8. [Asks: "Should I try opening in Safe Mode, or attempt recovery?"]
```

### Journey 3: Facebook Regression Test

```
User: "Run regression tests on our Facebook integration"

OSCAR:
1. [Launches Chrome, navigates to Facebook]
2. [Executes: Login flow (credentials from secure storage)]
3. [Perceives: Home feed visible]
4. [Executes: Navigate to Page → Posts
5. [Perceives: Post list loads]
6. [Executes: Create new post with test content]
7. [Verifies: Post appears in feed]
8. [Captures: Screenshot of post]
9. [Executes: Delete post (cleanup)]
10. [Reports: All tests passed, screenshots attached]
```

---

## 13. Future Research Directions

### 13.1 Neural UI Understanding

Train a multimodal model specifically on UI interactions:
- Input: (screenshot, accessibility tree, interaction history)
- Output: (next action, confidence, counterfactual)

### 13.2 Universal UI Grammar

Discover that all UIs follow a small set of grammatical patterns:
- "Dialogs are modals with [Action] [Cancel]"
- "Toolbars are horizontal icon strips"
- "Forms are labeled input groups"

If true, OSCAR needs fewer examples to generalize.

### 13.3 Embodied Agent Learning

Apply reinforcement learning where OSCAR:
- Receives reward for task completion
- Receives penalty for failures
- Learns efficient action sequences
- Transfers learned policies to new apps

### 13.4 Collaborative Agent Networks

Multiple OSCAR instances share learnings:
- App A learns about Photoshop
- App B learns about Excel
- Combined knowledge benefits all instances
- Privacy-preserving knowledge transfer via federated learning

---

## 14. Conclusion

OSCAR represents a fundamental advance in computer control — from brittle automation to intelligent, recoverable, and learnable system operation. By combining visual perception, semantic action spaces, and persistent memory, OSCAR can help users with any application, adapt to new interfaces automatically, and recover gracefully from failures.

The path from current capabilities to OSCAR's vision requires:
- **Research:** Solving visual grounding, transfer learning, and recovery
- **Engineering:** Building reliable cross-platform control
- **User Trust:** Demonstrating safety and reliability over time

This specification provides the roadmap. The implementation begins now.
