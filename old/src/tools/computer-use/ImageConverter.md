# Image to Text Converter

## Overview

The **Image to Text Converter** enables non-multimodal LLMs (like local Ollama models) to "see" screenshots by converting visual data into text-based representations, similar to how terminal browsers render websites.

## Key Features

- **Box Model Representation**: Converts UI elements to structured text with CSS-like box models
- **Terminal Grid Visualization**: Renders element positions as ASCII art grids
- **Spatial Awareness**: Provides precise coordinate and size information for UI elements
- **LLM-Friendly Format**: Structured text that local LLMs can understand without multimodal capabilities

## Problem Solved

Most local LLMs (Ollama, vLLM, etc.) don't support multimodal (image) inputs. When the owl takes a screenshot:

**Before**: The LLM cannot "see" the screenshot and just gets a file path

```
Screenshot saved to: /path/to/screen.png
```

**After**: The LLM receives detailed text analysis:

```
SCREENSHOT CAPTURED
===================
File: screen.png
Location: /path/to/screen.png
Size: 128.4 KB

--- SCREENSHOT ANALYSIS (for AI) ---
SCREEN ANALYSIS
==============
Resolution: 1920x1080
Scale factor: 2x
Elements found: 5

[1] AXButton — "Click Me"
    Position: (300, 250) Size: 100x40
    Center (click here): (350, 270)

[2] AXTextField — "Search..."
    Position: (50, 100) Size: 400x30

...

INSTRUCTIONS FOR AI
===================
Use this spatial information to understand what's visible on screen.
You can use computer_use tool to interact with UI elements by coordinate.
find_elements action can locate specific elements by text/role for precise targeting.
```

## API Reference

### `analyzeScreenshotToText(analysis: ScreenshotAnalysis): string`

Formats screenshot analysis as a structured text report.

**Returns**: Human-readable text with screen resolution, element count, and detailed element specs.

### `renderTerminalGrid(elements: ElementBox[], width?: number, height?: number): string`

Renders elements as an ASCII grid (like terminal browsers).

**Returns**: Visual representation with `█` characters showing element positions.

### `elementToBoxString(el: ElementBox): string`

Converts a single UI element to a formatted box string.

**Returns**:

```
┌──────────────────────────────────────────────────────────────────────┐
│ Element: AXButton (role: button) — "Click Me"                      │
├──────────────────────────────────────────────────────────────────────┤
│ Position: x= 100, y=  50                                             │
│ Size: 200x40                                                         │
└──────────────────────────────────────────────────────────────────────┘
```

### `extractScreenshotMetadata(imagePath: string): ScreenshotMetadata`

Extracts file metadata (path, filename, size) from an image file.

**Returns**: `{ path: string, filename: string, sizeBytes: number }`

### `formatScreenshotForLLM(metadata: ScreenshotMetadata, analysis?: ScreenshotAnalysis): string`

Formats screenshot for LLM consumption by combining metadata with optional analysis.

**Returns**: Complete LLM-friendly message body.

### `analyzeScreen(appName?: string): Promise<ScreenshotAnalysis>`

High-level analysis API that captures the screen and finds UI elements.

**Returns**: Full `ScreenshotAnalysis` object with resolution, scale, and elements.

## Types

### `ElementBox`

```typescript
interface ElementBox {
  element: string; // UI element type (e.g., "AXButton")
  role: string; // Accessibility role
  text?: string; // Element text content (optional)
  position: {
    // Position on screen
    x: number;
    y: number;
  };
  size: {
    // Element dimensions
    width: number;
    height: number;
  };
}
```

### `ScreenshotAnalysis`

```typescript
interface ScreenshotAnalysis {
  screen: {
    resolution: { width: number; height: number };
    scale: number; // Retina scaling factor (e.g., 2.0)
  };
  elements: ElementBox[];
  timestamp: number; //当 the screenshot was taken
}
```

## Usage Examples

### 1. Basic Screenshot with Analysis

```typescript
import * as mac from "./computer-use/macos.js";

// Take screenshot
const outPath = "/tmp/screenshot.png";
await mac.screenshot(outPath);

// Analyze for LLM
const metadata = mac.extractScreenshotMetadata(outPath);
const analysis = await mac.analyzeScreen("Safari");
const llmContent = mac.formatScreenshotForLLM(metadata, analysis);

console.log(llmContent);
// LLM can now "see" the screenshot!
```

### 2. Terminal Visualization

```typescript
const grid = renderTerminalGrid(elements, 80, 24);
console.log(grid);

// Output:
// Screen: 80x24
// ████───────████
// █░░░░░░░░░░░░░░█
// █░░░░░░░░░░░░░░█
// ████████████████
```

### 3. Element Box String

```typescript
const el: ElementBox = {
  element: "AXButton",
  role: "button",
  text: "Submit Form",
  position: { x: 400, y: 350 },
  size: { width: 120, height: 40 },
};

console.log(elementToBoxString(el));
// Formatted box with precise coordinates
```

## Integration with Computer Use Tool

The converter is automatically integrated into:

1. **`take_screenshot` tool**: Returns screenshot path + analysis
2. **`computer_use(action: 'screenshot')`**: Same enhanced output
3. **`send_file`**: Works with screenshot analysis metadata

### Example LLM Prompt After Screenshot

When the owl takes a screenshot during task execution:

```
You have executed: computer_use(action='screenshot')

RESULT:
Screenshot saved: /workspace/screenshots/screen_1709823456.png
Screen: 1920x1080 (scale: 2x)
Use send_file to deliver to user.

--- SCREENSHOT ANALYSIS (for AI) ---
SCREEN ANALYSIS
==============
Resolution: 1920x1080
Scale factor: 2x
Elements found: 3

[1] AXButton — "Save Changes"
    Position: (1600, 950) Size: 120x40

[2] AXTextField — "Document Title"
    Position: (100, 80) Size: 600x40

[3] AXWindow — "Untitled Document"
    Position: (10, 10) Size: 960x1070

[Instructions for interacting with elements...]
```

The LLM can now:

- Identify buttons by text/position
- Calculate click coordinates
- Decide which element to interact with next

## Text-Based UI Interpretation (Terminal Browser Analogy)

Just like terminal browsers (Links, Lynx, W3M) render HTML as text:

```
┌──────────────────────────────────────────────┐
│  [✓] Enable AI Assistant                     │
├──────────────────────────────────────────────┤
│  [  Email: ________________]                 │
│  [Password: ________________]                │
│                                               │
│          [   Login   ]  [ Register ]         │
└──────────────────────────────────────────────┘
```

This converter creates a similar representation for native UI elements, enabling text-based LLMs to understand and navigate desktop applications.

## Benefits for Local LLMs

| Capability  | Before                     | After                           |
| ----------- | -------------------------- | ------------------------------- |
| Vision      | ❌ Cannot see screenshots  | ✅ Understands UI layout        |
| Navigation  | ❌ No coordinate awareness | ✅ Precise element positions    |
| Interaction | ❌ Guessing only           | ✅ Targeted element interaction |
| Analysis    | ❌ Text-only context       | ✅ Visual content understanding |

## Performance Considerations

- **Instant**: No image processing library needed
- **Lightweight**: Pure TypeScript/Node.js
- **Zero Dependencies**: Uses native macOS accessibility API

## Testing

Run the test suite:

```bash
npm run test -- __tests__/image-converter.test.ts
```

All tests pass (9/9):

- ✅ Element box string formatting
- ✅ Text truncation for long content
- ✅ Terminal grid visualization
- ✅ Empty element handling
- ✅ Screenshot analysis formatting
- ✅ Element details inclusion
- ✅ File metadata extraction
- ✅ LLM-friendly formatting

## Future Enhancements

1. **Markdown Table Export**: Convert elements to markdown tables
2. **SVG Visualization**: Generate minimal SVG icons for elements
3. **OCR Integration**: Add optional OCR for image text extraction (multimodal fallback)
4. **Web Page Analysis**: HTML-to-ASCII converter for web navigation
5. **Element Tree**: Represent UI hierarchy as indented tree structure
