/**
 * StackOwl — Tool Quality Checklist (Element 7 T7)
 *
 * Verifies that the top-30 most-load-bearing tools all declare:
 *   - capabilities[]   (≥1 tag)   — feeds CWTG/SelfEvolver capability matching
 *   - executionPolicy.timeoutMs > 0   — bounds runaway tool calls
 *
 * Names below match what the registry actually exposes (post-7a unified
 * facades). `recall_memory` / `remember` are intentionally absent — both are
 * `deprecated: true` and routed through the unified `memory` tool instead.
 */
import { describe, it, expect } from "vitest";
import type { ToolImplementation } from "../../src/tools/registry.js";
import { ToolRegistry } from "../../src/tools/registry.js";

import { ShellTool } from "../../src/tools/shell.js";
import { SandboxTool } from "../../src/tools/sandbox.js";
import {
  ReadFileTool,
  WriteFileTool,
  EditFileTool,
} from "../../src/tools/files.js";
import { WebFetchTool } from "../../src/tools/web.js";
import { WebSearchTool } from "../../src/tools/search.js";
import { ScreenshotTool } from "../../src/tools/screenshot.js";
import { OrchestrateTasksTool } from "../../src/tools/orchestrate.js";
import { SummonParliamentTool } from "../../src/tools/parliament.js";
import { PelletRecallTool } from "../../src/tools/pellet-recall.js";
import { JSONTransformTool } from "../../src/tools/utils/json-transform.js";
import { ClipboardTool } from "../../src/tools/macos/clipboard.js";
import { AppleCalendarTool } from "../../src/tools/macos/calendar.js";
import { AppleRemindersTool } from "../../src/tools/macos/reminders.js";
import { AppleMailTool } from "../../src/tools/macos/mail.js";
import { MermaidDiagramTool } from "../../src/tools/creative/mermaid-diagram.js";
import { MarkdownRenderTool } from "../../src/tools/creative/markdown-render.js";
import { ImageGenerationTool } from "../../src/tools/creative/image-generation.js";
import { SpeechToTextTool } from "../../src/tools/creative/speech-to-text.js";
import { SpreadsheetTool } from "../../src/tools/data/spreadsheet.js";
import { DataVisualizationTool } from "../../src/tools/data/data-viz.js";
import { OCRTool } from "../../src/tools/data/ocr.js";
import { PDFReaderTool } from "../../src/tools/data/pdf-reader.js";

import { createMemoryUnifiedTool } from "../../src/tools/memory-unified.js";
import { VisionTool } from "../../src/tools/vision.js";
import { DocumentTool } from "../../src/tools/document.js";
import { CodeSandboxTool } from "../../src/tools/code-sandbox.js";
import { DbQueryTool } from "../../src/tools/db-query.js";
import { ScheduleTool } from "../../src/tools/schedule.js";

const REQUIRED_TOP_30 = [
  "web_fetch",
  "web_search",
  "memory",
  "read_file",
  "write_file",
  "edit_file",
  "run_shell_command",
  "vision",
  "document",
  "sandbox",
  "db_query",
  "schedule",
  "summon_parliament",
  "orchestrate_tasks",
  "pellet_recall",
  "mermaid_diagram",
  "markdown_render",
  "image_generation",
  "speech_to_text",
  "spreadsheet",
  "data_visualization",
  "json_transform",
  "ocr",
  "pdf_reader",
  "take_screenshot",
  "clipboard",
  "apple_mail",
  "apple_calendar",
  "apple_reminders",
] as const;

function buildTestRegistry(): ToolRegistry {
  const registry = new ToolRegistry();
  const tools: ToolImplementation[] = [
    ShellTool,
    SandboxTool,
    ReadFileTool,
    WriteFileTool,
    EditFileTool,
    WebFetchTool,
    WebSearchTool,
    ScreenshotTool,
    OrchestrateTasksTool,
    new SummonParliamentTool(),
    PelletRecallTool,
    JSONTransformTool,
    ClipboardTool,
    AppleCalendarTool,
    AppleRemindersTool,
    AppleMailTool,
    MermaidDiagramTool,
    MarkdownRenderTool,
    ImageGenerationTool,
    SpeechToTextTool,
    SpreadsheetTool,
    DataVisualizationTool,
    OCRTool,
    PDFReaderTool,
    createMemoryUnifiedTool(),
    VisionTool,
    DocumentTool,
    CodeSandboxTool,
    DbQueryTool,
    ScheduleTool,
  ];
  for (const t of tools) registry.register(t);
  return registry;
}

describe("Tool quality checklist — top 30", () => {
  const registry = buildTestRegistry();

  it.each(REQUIRED_TOP_30)(
    "%s declares capabilities[] and executionPolicy.timeoutMs",
    (toolName) => {
      const def = registry.getDefinition(toolName);
      expect(def, `${toolName} not registered`).toBeDefined();
      expect(
        def!.capabilities,
        `${toolName} missing capabilities[]`,
      ).toBeDefined();
      expect(
        def!.capabilities!.length,
        `${toolName} has empty capabilities[]`,
      ).toBeGreaterThan(0);
      expect(
        def!.executionPolicy?.timeoutMs,
        `${toolName} missing executionPolicy.timeoutMs`,
      ).toBeGreaterThan(0);
    },
  );
});
