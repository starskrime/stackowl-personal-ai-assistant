---
name: text_select_all
description: Select all text, select specific text ranges, select lines, select words in any application
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "📝"
  os: [darwin]
parameters:
  action:
    type: string
    description: "Action: all, line, word, paragraph, or invert"
    default: "all"
  count:
    type: number
    description: "Number of words/lines to select"
    default: 1
  direction:
    type: string
    description: "Direction for extend selection: left, right, up, down"
    default: "right"
required: []
steps:
  - id: select_all
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"System Events\" to keystroke \"a\" using command down' && echo 'Selected all'"
      mode: "local"
    timeout_ms: 5000
  - id: select_line
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"System Events\" to keystroke \"a\" using command down' && echo 'Selected line'"
      mode: "local"
    timeout_ms: 5000
  - id: select_word
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"System Events\" to keystroke \"a\" using option down' && echo 'Selected word'"
      mode: "local"
    timeout_ms: 5000
  - id: select_paragraph
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"System Events\" to keystroke \"a\" using {command down, option down}' && echo 'Selected paragraph'"
      mode: "local"
    timeout_ms: 5000
  - id: extend_selection_right
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"System Events\" to key code 124 using {shift down}' && echo 'Extended selection right'"
      mode: "local"
    timeout_ms: 5000
  - id: extend_selection_left
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"System Events\" to key code 123 using {shift down}' && echo 'Extended selection left'"
      mode: "local"
    timeout_ms: 5000
  - id: extend_selection_up
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"System Events\" to key code 126 using {shift down}' && echo 'Extended selection up'"
      mode: "local"
    timeout_ms: 5000
  - id: extend_selection_down
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"System Events\" to key code 125 using {shift down}' && echo 'Extended selection down'"
      mode: "local"
    timeout_ms: 5000
  - id: select_to_start
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"System Events\" to key code 123 using {command down, shift down}' && echo 'Selected to line start'"
      mode: "local"
    timeout_ms: 5000
  - id: select_to_end
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"System Events\" to key code 124 using {command down, shift down}' && echo 'Selected to line end'"
      mode: "local"
    timeout_ms: 5000
  - id: analyze
    type: llm
    prompt: "Text selection: '{{action}}'\n\nSelection completed."
    depends_on: [select_all]
    inputs: [select_all.output]
---

# Text Select All

Advanced text selection in any application.

## Usage

Select all:
```
/text_select_all
```

Select line:
```
action=line
```

Extend selection:
```
action=extend
direction=right
count=5
```

## Actions

- **all**: Select all text (⌘A)
- **line**: Select current line (⌘A twice in most apps)
- **word**: Select word (⌥A)
- **paragraph**: Select paragraph (⌘⌥A)
- **extend**: Extend selection in direction

## Direction for Extend

- **right**: Extend right (→)
- **left**: Extend left (←)
- **up**: Extend up (↑)
- **down**: Extend down (↓)

## Examples

### Select all text
```
action=all
```

### Select current line
```
action=line
```

### Select 10 words right
```
action=extend
direction=right
count=10
```

### Select to end of line
```
action=to_end
```

## Key Combinations

| Action | Keys |
|--------|------|
| Select All | ⌘A |
| Select Word | ⌥A |
| Select Line | ⌘A (double) |
| Extend Char | ⇧→ |
| Extend Word | ⇧⌥→ |
| To Line Start | ⇧⌘← |
| To Line End | ⇧⌘→ |