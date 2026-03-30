---
name: text_delete
description: Delete text with precision - characters, words, lines, or all text with undo support
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "⌫"
  os: [darwin]
parameters:
  action:
    type: string
    description: "Action: char, word, line, all, backspace, forward"
    default: "char"
  count:
    type: number
    description: "Number of units to delete"
    default: 1
required: []
steps:
  - id: delete_char_back
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"System Events\" to key code 51' && echo 'Deleted'"
      mode: "local"
    timeout_ms: 5000
  - id: delete_word
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"System Events\" to keystroke \"d\" using {option down}' && echo 'Deleted word'"
      mode: "local"
    timeout_ms: 5000
  - id: delete_line
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"System Events\" to keystroke \"k\" using {command down}' && echo 'Deleted line'"
      mode: "local"
    timeout_ms: 5000
  - id: delete_all
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"System Events\" to keystroke \"a\" using command down' -e 'tell application \"System Events\" to key code 51' && echo 'Deleted all'"
      mode: "local"
    timeout_ms: 5000
  - id: delete_forward
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"System Events\" to key code 117' && echo 'Deleted forward'"
      mode: "local"
    timeout_ms: 5000
  - id: delete_to_start
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"System Events\" to keystroke \"u\" using {command down}' && echo 'Deleted to start'"
      mode: "local"
    timeout_ms: 5000
  - id: delete_to_end
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"System Events\" to keystroke \"k\" using {command down}' && echo 'Deleted to end'"
      mode: "local"
    timeout_ms: 5000
  - id: delete_repeated
    tool: ShellTool
    args:
      command: "for i in $(seq 1 {{count}}); do osascript -e 'tell application \"System Events\" to key code 51'; done && echo 'Deleted {{count}} chars'"
      mode: "local"
    timeout_ms: 30000
  - id: undo_delete
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"System Events\" to keystroke \"z\" using command down' && echo 'Undo'"
      mode: "local"
    timeout_ms: 5000
  - id: analyze
    type: llm
    prompt: "Delete action: '{{action}}'\n\nCount: {{count}}\n\nCompleted."
    depends_on: [delete_char_back]
    inputs: [delete_char_back.output]
---

# Text Delete

Delete text with precision.

## Usage

Delete character:
```
action=char
```

Delete word:
```
action=word
```

Delete 5 characters:
```
action=char
count=5
```

Delete to end of line:
```
action=to_end
```

## Actions

- **char**: Delete character backward (⌫)
- **forward**: Delete character forward (⌦)
- **word**: Delete word (⌥⌫)
- **line**: Delete line (⌘K)
- **all**: Delete all (⌘A, ⌫)
- **to_start**: Delete to line start (⌘⌫)
- **to_end**: Delete to line end (⌘K)

## Examples

### Delete one char
```
action=char
```

### Delete word
```
action=word
```

### Delete 10 chars
```
action=char
count=10
```

### Delete entire line
```
action=line
```

## Notes

- Undo available with ⌘Z
- Works in any text field
- Repeat count for multiple units