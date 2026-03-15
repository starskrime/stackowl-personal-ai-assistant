/**
 * StackOwl — Screen Reader (Image-to-Text for Text-Only Models)
 *
 * Converts the visible screen state into a compact text representation
 * using the macOS Accessibility API. This lets text-only models "see"
 * the screen without sending images — saving tokens and working with
 * any model, not just vision models.
 *
 * Architecture:
 *   1. Deep Accessibility Walk — reads the FULL UI tree including text
 *      content, values, labels, roles, and positions
 *   2. Hierarchical Formatting — preserves parent-child structure
 *      (menubar > menu > item, toolbar > button, web area > text)
 *   3. Numbered References — every interactive element gets a [ref:N]
 *      with click coordinates, so the LLM can say "click ref 5"
 *   4. Compact Output — aggressive deduplication and truncation to
 *      keep token count low (~500-2000 tokens vs ~5000+ for an image)
 */

import { spawn } from "node:child_process";

const TIMEOUT = 20_000;

// ─── Types ───────────────────────────────────────────────────────────────────

export interface ScreenElement {
  ref?: number;           // Numbered reference for interactive elements
  role: string;           // AXRole (AXButton, AXTextField, AXStaticText, etc.)
  label: string;          // Title or description
  value?: string;         // Current value (text field content, checkbox state, etc.)
  position: { x: number; y: number };
  size: { width: number; height: number };
  children?: ScreenElement[];
  interactable: boolean;  // Can be clicked/typed into
}

export interface ScreenState {
  app: string;            // Front application name
  windowTitle: string;    // Active window title
  screen: { width: number; height: number; scale: number };
  elements: ScreenElement[];
  interactiveCount: number;
  timestamp: number;
}

// Note: Role classification is done inside the JXA script (see readScreen)
// to avoid multiple roundtrips. The categories are:
// INTERACTIVE: AXButton, AXTextField, AXTextArea, AXLink, AXCheckBox, etc.
// TEXT_BEARING: AXStaticText, AXTextField, AXTextArea, AXHeading, AXLink, etc.
// SKIP: AXScrollBar, AXSplitter, AXGrowArea, AXRuler, etc.

// ─── JXA Helper ──────────────────────────────────────────────────────────────

async function jxa(script: string): Promise<string> {
  return new Promise((resolve, reject) => {
    const proc = spawn("osascript", ["-l", "JavaScript"], {
      stdio: ["pipe", "pipe", "pipe"],
      timeout: TIMEOUT,
    });
    let stdout = "";
    let stderr = "";
    proc.stdout.on("data", (d: Buffer) => { stdout += d.toString(); });
    proc.stderr.on("data", (d: Buffer) => { stderr += d.toString(); });
    proc.on("close", (code) => {
      if (code !== 0) reject(new Error(stderr.trim() || `osascript exited ${code}`));
      else resolve(stdout.trim());
    });
    proc.on("error", reject);
    proc.stdin.write(script);
    proc.stdin.end();
  });
}

// ─── Deep Screen Reader ─────────────────────────────────────────────────────

/**
 * Read the full screen state of the front application (or a named app).
 * Returns a structured ScreenState with all visible elements, their text
 * content, positions, and numbered references for interactive elements.
 */
export async function readScreen(appName?: string): Promise<ScreenState> {
  // Single JXA call that does the entire deep walk — much faster than
  // multiple roundtrips. Returns JSON with the full element tree.
  const result = await jxa(`
    ObjC.import('AppKit');

    var SE = Application('System Events');
    var targetApp = ${appName ? `'${appName.replace(/'/g, "\\'")}'` : "null"};

    // Get front app if not specified
    var proc;
    if (targetApp) {
      proc = SE.processes.byName(targetApp);
    } else {
      var frontProcs = SE.processes.whose({frontmost: true});
      proc = frontProcs[0];
      targetApp = proc.name();
    }

    // Screen info
    var screen = $.NSScreen.mainScreen;
    var frame = screen.frame;
    var scale = screen.backingScaleFactor;

    // Window title
    var windowTitle = '';
    try {
      var wins = proc.windows();
      if (wins.length > 0) {
        windowTitle = wins[0].title() || '';
      }
    } catch(e) {}

    var refCounter = 0;
    var maxElements = 200; // Cap to prevent explosion on complex UIs
    var totalElements = 0;

    var INTERACTIVE = {
      'AXButton':1, 'AXTextField':1, 'AXTextArea':1, 'AXLink':1,
      'AXCheckBox':1, 'AXRadioButton':1, 'AXPopUpButton':1,
      'AXComboBox':1, 'AXSlider':1, 'AXMenuButton':1, 'AXMenuItem':1,
      'AXTab':1, 'AXTabGroup':1, 'AXDisclosureTriangle':1,
      'AXIncrementor':1, 'AXSearchField':1
    };
    var TEXT_BEARING = {
      'AXStaticText':1, 'AXTextField':1, 'AXTextArea':1,
      'AXHeading':1, 'AXLink':1, 'AXCell':1, 'AXSearchField':1
    };
    var SKIP = {
      'AXScrollBar':1, 'AXSplitter':1, 'AXGrowArea':1,
      'AXRuler':1, 'AXLayoutArea':1, 'AXMatte':1, 'AXUnknown':1
    };

    function scanElement(el, depth) {
      if (totalElements >= maxElements || depth > 8) return null;
      try {
        var role = el.role();
        if (SKIP[role]) return null;

        totalElements++;

        var label = '';
        try { label = el.title() || ''; } catch(e) {}
        if (!label) {
          try { label = el.description() || ''; } catch(e) {}
        }

        // Get VALUE — this is the key for text-only models
        // (text field contents, checkbox state, slider value, URL bar, etc.)
        var value = null;
        if (TEXT_BEARING[role]) {
          try {
            var v = el.value();
            if (v !== null && v !== undefined && String(v).length > 0) {
              value = String(v).slice(0, 500);
            }
          } catch(e) {}
        }

        // Position and size
        var pos = {x:0, y:0};
        var sz = {width:0, height:0};
        try { var p = el.position(); pos = {x:p[0], y:p[1]}; } catch(e) {}
        try { var s = el.size(); sz = {width:s[0], height:s[1]}; } catch(e) {}

        // Skip zero-size elements
        if (sz.width === 0 && sz.height === 0 && !value && !label) return null;

        var isInteractive = !!INTERACTIVE[role];
        var ref = isInteractive ? (++refCounter) : undefined;

        // Scan children (but limit depth for web content which can be very deep)
        var maxChildDepth = (role === 'AXWebArea') ? depth + 3 : depth + 1;
        var children = [];
        try {
          var childEls = el.uiElements();
          for (var i = 0; i < childEls.length && totalElements < maxElements; i++) {
            var child = scanElement(childEls[i], maxChildDepth);
            if (child) children.push(child);
          }
        } catch(e) {}

        // Skip container elements that have no label, no value, and only 1 child
        // (just noise wrappers)
        if (!label && !value && !isInteractive && children.length === 1) {
          return children[0];
        }

        // Skip empty containers
        if (!label && !value && !isInteractive && children.length === 0) {
          return null;
        }

        var result = {
          role: role,
          label: label,
          interactable: isInteractive,
          position: pos,
          size: sz
        };
        if (ref) result.ref = ref;
        if (value) result.value = value;
        if (children.length > 0) result.children = children;

        return result;
      } catch(e) {
        return null;
      }
    }

    // Scan all windows (up to 3)
    var allElements = [];
    try {
      var wins = proc.windows();
      for (var w = 0; w < wins.length && w < 3; w++) {
        var el = scanElement(wins[w], 0);
        if (el) allElements.push(el);
      }
    } catch(e) {}

    JSON.stringify({
      app: targetApp,
      windowTitle: windowTitle,
      screen: {
        width: frame.size.width,
        height: frame.size.height,
        scale: scale
      },
      elements: allElements,
      interactiveCount: refCounter,
      timestamp: Date.now()
    });
  `);

  return JSON.parse(result);
}

// ─── Compact Text Formatter ─────────────────────────────────────────────────

/**
 * Convert a ScreenState to a compact text representation optimized for LLMs.
 * Produces ~500-2000 tokens depending on screen complexity.
 *
 * Format:
 *   APP: Safari | WINDOW: Google Search
 *   SCREEN: 3008x1692 (2x)
 *
 *   [ref:1] button "Back" @ (189,56)
 *   [ref:2] textfield "Address Bar" = "https://google.com" @ (1489,64)
 *   text "Search results for: stackowl"
 *   [ref:3] link "StackOwl - GitHub" @ (200,300)
 *   text "A personal AI assistant framework..."
 */
export function formatScreenAsText(state: ScreenState): string {
  const lines: string[] = [];

  // Header
  lines.push(`APP: ${state.app} | WINDOW: ${state.windowTitle || "(untitled)"}`);
  lines.push(`SCREEN: ${state.screen.width}x${state.screen.height} (${state.screen.scale}x)`);
  lines.push(`INTERACTIVE ELEMENTS: ${state.interactiveCount} (use ref number to click)`);
  lines.push("");

  // Flatten and format elements
  formatElements(state.elements, lines, 0);

  // Instructions footer
  lines.push("");
  lines.push("---");
  lines.push("To interact: computer_use(action:'click', x:X, y:Y) using coordinates above.");
  lines.push("To click by ref: find the [ref:N] element and use its coordinates.");
  lines.push("To type: first click a text field, then computer_use(action:'type', text:'...').");

  return lines.join("\n");
}

function formatElements(
  elements: ScreenElement[],
  lines: string[],
  indent: number,
): void {
  const pad = "  ".repeat(indent);

  for (const el of elements) {
    const roleName = el.role.replace("AX", "").toLowerCase();

    if (el.interactable && el.ref) {
      // Interactive element — show ref number and click coordinates
      const cx = Math.round(el.position.x + el.size.width / 2);
      const cy = Math.round(el.position.y + el.size.height / 2);
      let line = `${pad}[ref:${el.ref}] ${roleName}`;
      if (el.label) line += ` "${truncate(el.label, 60)}"`;
      if (el.value) line += ` = "${truncate(el.value, 80)}"`;
      line += ` @ (${cx},${cy})`;
      lines.push(line);
    } else if (el.value || el.label) {
      // Text content — show inline
      const text = el.value || el.label;
      if (text.trim()) {
        lines.push(`${pad}${roleName}: "${truncate(text, 120)}"`);
      }
    } else if (el.children && el.children.length > 0) {
      // Container — show role as section header only if it has a label
      if (el.label) {
        lines.push(`${pad}── ${roleName}: ${el.label} ──`);
      }
    }

    // Recurse into children
    if (el.children && el.children.length > 0) {
      const childIndent = el.label ? indent + 1 : indent;
      formatElements(el.children, lines, childIndent);
    }
  }
}

// ─── Minimal Format (Ultra-Compact) ─────────────────────────────────────────

/**
 * Even more compact format — only interactive elements and significant text.
 * Use when token budget is very tight (~200-500 tokens).
 */
export function formatScreenMinimal(state: ScreenState): string {
  const lines: string[] = [];
  lines.push(`[${state.app}] ${state.windowTitle || ""}`);

  flattenInteractive(state.elements, lines);

  return lines.join("\n");
}

function flattenInteractive(elements: ScreenElement[], lines: string[]): void {
  for (const el of elements) {
    if (el.interactable && el.ref) {
      const cx = Math.round(el.position.x + el.size.width / 2);
      const cy = Math.round(el.position.y + el.size.height / 2);
      const name = el.label || el.value || el.role.replace("AX", "");
      lines.push(`[${el.ref}] ${name} (${cx},${cy})`);
    } else if (el.value && el.value.length > 5) {
      // Significant text content
      lines.push(`  "${truncate(el.value, 100)}"`);
    }

    if (el.children) flattenInteractive(el.children, lines);
  }
}

// ─── Helper ──────────────────────────────────────────────────────────────────

function truncate(s: string, max: number): string {
  if (s.length <= max) return s;
  return s.slice(0, max - 3) + "...";
}
