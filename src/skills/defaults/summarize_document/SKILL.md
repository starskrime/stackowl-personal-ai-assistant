---
name: summarize_document
description: Create a concise summary of a long document extracting key points, themes, and conclusions
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "📑"
parameters:
  file_path:
    type: string
    description: "Path to document to summarize (PDF, TXT, MD)"
  length:
    type: string
    description: "Summary length (brief, medium, detailed)"
    default: "medium"
required: [file_path]
steps:
  - id: read_document
    tool: ReadFileTool
    args:
      path: "{{file_path}}"
  - id: extract_pdf
    tool: ShellTool
    args:
      command: "pdftotext {{file_path}} - 2>/dev/null || cat {{file_path}}"
      mode: "local"
    timeout_ms: 30000
    optional: true
  - id: generate_summary
    type: llm
    prompt: "Create a concise summary of this document with:\n- One-paragraph executive summary\n- 5-7 key points\n- Main conclusions\n- Recommended actions (if applicable)\n\nSummary length: {{length}}\n\nDocument content:\n{{#if extract_pdf.output}}{{extract_pdf.output}}{{else}}{{read_document.output}}{{/if}}"
    depends_on: [read_document, extract_pdf]
    inputs: [read_document.output, extract_pdf.output]
---

# Summarize Document

Condense long documents into key takeaways.

## Usage

```bash
/summarize_document file_path="report.pdf" length=medium
```

## Parameters

- **file_path**: Path to document to summarize (PDF, TXT, MD)
- **length**: Summary length (brief, medium, detailed, default: medium)

## Error Handling

- **Very long document (>50 pages):** Process in sections, summarize each, then create meta-summary.
- **Binary file:** Convert first using appropriate tool.
