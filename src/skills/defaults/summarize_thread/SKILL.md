---
name: summarize_thread
description: Summarize a long email thread or chat conversation into key points, decisions, and action items
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "📌"
parameters:
  file_path:
    type: string
    description: "Path to the thread file (leave empty to paste text)"
  text:
    type: string
    description: "Thread text content (use this or file_path)"
  primary_language:
    type: string
    description: "Primary language of the thread"
    default: "en"
required: []
steps:
  - id: read_file
    tool: ReadFileTool
    args:
      path: "{{file_path}}"
    optional: true
  - id: generate_summary
    type: llm
    prompt: "Summarize the following email/chat thread:\n\nThread content:\n{{text || read_file.output}}\n\nExtract and structure:\n- **Key points** (3–5 bullet points)\n- **Decisions made** (if any)\n- **Action items** with owners\n- **Unresolved questions**\n- **Overall sentiment/tone**\n\nIf thread is longer than 10000 chars, process in chunks and create a meta-summary."
    depends_on: [read_file]
    inputs: [text, read_file.output, primary_language]
---

# Summarize Thread

Condense a long email or chat thread into a structured summary.

## Usage

```bash
/summarize_thread file_path=~/Downloads/email_thread.txt
/summarize_thread text="Long email thread content..." primary_language=en
```

## Parameters

- **file_path**: Path to the thread file (leave empty to paste text)
- **text**: Thread text content (use this or file_path)
- **primary_language**: Primary language of the thread (default: en)

## Examples

### Summarize from a file

```bash
read_file("~/Downloads/long_email_thread.txt")
```

## Error Handling

- **Thread too long (>10000 chars):** Process in chunks, summarize each, then create meta-summary.
- **No clear action items:** Note "No explicit action items identified."
- **Multiple languages in thread:** Translate non-primary language sections first.
