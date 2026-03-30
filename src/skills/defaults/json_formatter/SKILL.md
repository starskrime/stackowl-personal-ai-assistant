---
name: json_formatter
description: Format, validate, and pretty-print JSON data from files, clipboard, or inline input
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "📐"
parameters:
  source:
    type: string
    description: "Source: file path, clipboard, or inline"
  inline_json:
    type: string
    description: "Inline JSON string to format"
  save_to:
    type: string
    description: "Optional file path to save formatted output"
required: []
steps:
  - id: read_file
    tool: ReadFileTool
    args:
      path: "{{source}}"
    optional: true
  - id: get_clipboard
    tool: ShellTool
    args:
      command: "pbpaste"
      mode: "local"
    timeout_ms: 5000
    optional: true
  - id: format_json
    tool: ShellTool
    args:
      command: "echo '{{inline_json}}' | python3 -m json.tool"
      mode: "local"
    timeout_ms: 10000
    optional: true
  - id: save_formatted
    tool: ShellTool
    args:
      command: "python3 -m json.tool < {{source}} > {{save_to}}"
      mode: "local"
    timeout_ms: 10000
    optional: true
---

# JSON Formatter

Validate and format JSON data.

## Usage

```bash
/json_formatter source=<path> inline_json=<json> save_to=<output>
```

## Parameters

- **source**: Source: file path, clipboard, or inline
- **inline_json**: Inline JSON string to format
- **save_to**: Optional file path to save formatted output

## Examples

### Format a JSON file

```
source=data.json
save_to=data_formatted.json
```

### Validate clipboard JSON

```
source=clipboard
```

## Error Handling

- **Invalid JSON:** Show the error line/position and suggest fixes.
- **Very large file:** Use streaming parser or process in chunks.
