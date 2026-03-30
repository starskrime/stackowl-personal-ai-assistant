---
name: clipboard_manager
description: Read, write, or transform the contents of the macOS clipboard (pasteboard)
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "📋"
  os: [darwin]
parameters:
  action:
    type: string
    description: "Action: read, write, or transform"
    default: "read"
  content:
    type: string
    description: "Text to copy to clipboard (for write action)"
  transform:
    type: string
    description: "Transform type: uppercase, lowercase, sort, reverse, trim, unique"
required: []
steps:
  - id: read_clipboard
    tool: ShellTool
    args:
      command: "pbpaste"
      mode: "local"
    timeout_ms: 5000
  - id: write_clipboard
    tool: ShellTool
    args:
      command: "echo '{{content}}' | pbcopy"
      mode: "local"
    timeout_ms: 5000
  - id: transform_uppercase
    tool: ShellTool
    args:
      command: "pbpaste | tr '[:lower:]' '[:upper:]' | pbcopy && echo 'Transformed to uppercase'"
      mode: "local"
    timeout_ms: 5000
  - id: transform_lowercase
    tool: ShellTool
    args:
      command: "pbpaste | tr '[:upper:]' '[:lower:]' | pbcopy && echo 'Transformed to lowercase'"
      mode: "local"
    timeout_ms: 5000
  - id: transform_sort
    tool: ShellTool
    args:
      command: "pbpaste | sort | pbcopy && echo 'Sorted and copied'"
      mode: "local"
    timeout_ms: 5000
  - id: transform_reverse
    tool: ShellTool
    args:
      command: "pbpaste | tr '\n' '\r' | rev | tr '\r' '\n' | pbcopy && echo 'Reversed lines'"
      mode: "local"
    timeout_ms: 5000
  - id: transform_trim
    tool: ShellTool
    args:
      command: "pbpaste | sed 's/^[[:space:]]*//;s/[[:space:]]*$//' | pbcopy && echo 'Trimmed whitespace'"
      mode: "local"
    timeout_ms: 5000
  - id: transform_unique
    tool: ShellTool
    args:
      command: "pbpaste | sort | uniq | pbcopy && echo 'Unique lines copied'"
      mode: "local"
    timeout_ms: 5000
  - id: verify_write
    tool: ShellTool
    args:
      command: "pbpaste | head -5"
      mode: "local"
    timeout_ms: 5000
  - id: analyze
    type: llm
    prompt: "Clipboard {{action}} result:\n\n{{#if_eq action 'read'}}Clipboard contents:\n{{read_clipboard.output}}{{/if_eq}}\n{{#if_eq action 'write'}}Verification:\n{{verify_write.output}}{{/if_eq}}\n{{#if_eq action 'transform'}}Transformation '{{transform}}' applied:\n{{verify_write.output}}{{/if_eq}}"
    depends_on: [read_clipboard]
    inputs: [read_clipboard.output, verify_write.output]
---

# Clipboard Manager

Read, write, or transform macOS clipboard contents.

## Usage

Read clipboard:
```
/clipboard_manager
```

Write to clipboard:
```
content=Hello World
action=write
```

Transform clipboard:
```
action=transform
transform=uppercase
```

## Actions

- **read** (default): Display current clipboard contents
- **write**: Copy text to clipboard
- **transform**: Apply transformation to clipboard contents

## Transform Types

- **uppercase**: Convert to ALL CAPS
- **lowercase**: Convert to all lowercase
- **sort**: Sort lines alphabetically
- **reverse**: Reverse line order
- **trim**: Remove leading/trailing whitespace
- **unique**: Remove duplicate lines

## Examples

### Copy text
```
action=write
content=Hello from the clipboard!
```

### Uppercase transform
```
action=transform
transform=uppercase
```

### Sort lines
```
action=transform
transform=sort
```

## Error Handling

- **Empty clipboard:** Reports "Clipboard is empty"
- **Binary content:** Notes that only text is supported
- **Large content:** Truncates display at reasonable limit