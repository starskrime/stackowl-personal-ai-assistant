---
name: proofread_text
description: Check text for grammar, spelling, punctuation, and style errors with suggested corrections
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "📝"
parameters:
  file_path:
    type: string
    description: "Path to the file to proofread (leave empty to paste text directly)"
  text:
    type: string
    description: "Text to proofread (use this or file_path)"
required: []
steps:
  - id: read_file
    tool: ReadFileTool
    args:
      path: "{{file_path}}"
    optional: true
  - id: analyze_text
    type: llm
    prompt: "Proofread the following text for grammar, spelling, punctuation, and style errors. Present corrections as a diff with ~~incorrect~~ → **corrected** format with explanations.\n\nText to proofread: {{text || read_file.output}}"
    depends_on: [read_file]
    inputs: [text, read_file.output]
  - id: apply_fixes
    tool: WriteFileTool
    args:
      path: "{{file_path}}"
      content: "{{analyze_text.output}}"
    optional: true
    depends_on: [analyze_text]
---

# Proofread Text

Check text for grammar and style issues.

## Usage

```bash
/proofread_text text="Your text here"
/proofread_text file_path=./document.md
```

## Parameters

- **file_path**: Path to the file to proofread (leave empty to paste text directly)
- **text**: Text to proofread (use this or file_path)

## Examples

### Proofread a document

```bash
read_file("~/Documents/report.md")
```

## Error Handling

- **Very long document:** Process section by section.
- **Multiple languages:** Ask which language to proofread in.
