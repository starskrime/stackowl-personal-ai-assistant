---
name: text_find
description: Find text in any application - search within documents, browsers, code editors with match highlighting
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🔎"
  os: [darwin]
parameters:
  action:
    type: string
    description: "Action: find, find_next, find_previous, replace"
    default: "find"
  search_text:
    type: string
    description: "Text to search for"
  replace_text:
    type: string
    description: "Text to replace with (for replace action)"
  app_name:
    type: string
    description: "Target application"
    default: "System Events"
required: [search_text]
steps:
  - id: find_dialog
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"System Events\" to keystroke \"f\" using command down'"
    mode: "local"
    timeout_ms: 5000
  - id: type_search
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"System Events\" to keystroke \"{{search_text}}\"'"
    mode: "local"
    timeout_ms: 5000
  - id: find_next
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"System Events\" to keystroke \"g\" using command down'"
    mode: "local"
    timeout_ms: 5000
  - id: find_previous
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"System Events\" to keystroke \"g\" using {command down, shift down}'"
    mode: "local"
    timeout_ms: 5000
  - id: replace_all
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"System Events\" to keystroke \"f\" using command down' && osascript -e 'tell application \"System Events\" to keystroke \"{{search_text}}\"' && osascript -e 'tell application \"System Events\" to keystroke \"e\" using {command down, option down}'"
    mode: "local"
    timeout_ms: 5000
  - id: grep_files
    tool: ShellTool
    args:
      command: "grep -rn '{{search_text}}' . 2>/dev/null | head -30"
    mode: "local"
    timeout_ms: 15000
  - id: analyze
    type: llm
    prompt: "Find operation: '{{search_text}}'\n\nSearch completed."
    depends_on: [find_dialog]
    inputs: [grep_files.output]
---

# Text Find

Find and replace text in any application.

## Usage

Open Find dialog:
```
/text_find
```

Search in files:
```
search_text=function
action=find
```

## Actions

- **find**: Open find dialog / search in files
- **find_next**: Find next occurrence
- **find_previous**: Find previous
- **replace**: Replace text

## Parameters

- **search_text**: Text to search for
- **replace_text**: Replacement text
- **app_name**: Target application

## Examples

### Open Find in app
```
search_text=hello
action=find
```

### Find in files
```
search_text=TODO
action=find
```

### Replace all
```
search_text=old
replace_text=new
action=replace
```

## Keyboard Shortcuts

| Action | Keys |
|--------|------|
| Find | ⌘F |
| Find Next | ⌘G |
| Find Previous | ⌘⇧G |
| Replace | ⌘⌥F |
| Replace All | ⌘⌥G |