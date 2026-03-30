---
name: keyboard_control
description: Type text, press key combinations, and execute keyboard shortcuts on macOS
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "⌨️"
  os: [darwin]
parameters:
  action:
    type: string
    description: "Action: type, key, shortcut, or hotkey"
    default: "type"
  text:
    type: string
    description: "Text to type (for type action)"
  key:
    type: string
    description: "Key name (e.g., return, space, delete, enter)"
  modifiers:
    type: string
    description: "Modifiers: cmd, shift, opt, ctrl (comma-separated)"
    default: ""
  key_code:
    type: number
    description: "Key code number (for direct key press)"
required: []
steps:
  - id: type_text
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"System Events\" to keystroke \"{{text}}\"'"
      mode: "local"
    timeout_ms: 5000
  - id: press_key
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"System Events\" to keystroke \"{{key}}\"'"
      mode: "local"
    timeout_ms: 5000
  - id: press_keycode
    tool: ShellTool
    args:
      command: "/usr/bin/python3 -c 'import Quartz; Quartz.CGEvent.post(Quartz.kCGHIDEventTap, Quartz.CGEventCreateKeyboardEvent(None, {{key_code}}, True)); Quartz.CGEvent.post(Quartz.kCGHIDEventTap, Quartz.CGEventCreateKeyboardEvent(None, {{key_code}}, False))'"
      mode: "local"
    timeout_ms: 5000
  - id: shortcut_cmd
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"System Events\" to keystroke \"{{key}}\" using command down'"
      mode: "local"
    timeout_ms: 5000
  - id: shortcut_shift_cmd
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"System Events\" to keystroke \"{{key}}\" using {command down, shift down}'"
      mode: "local"
    timeout_ms: 5000
  - id: shortcut_opt
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"System Events\" to keystroke \"{{key}}\" using option down'"
      mode: "local"
    timeout_ms: 5000
  - id: shortcut_ctrl
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"System Events\" to keystroke \"{{key}}\" using control down'"
      mode: "local"
    timeout_ms: 5000
  - id: analyze
    type: llm
    prompt: "Keyboard action: '{{action}}'\n\n{{#if text}}Text: {{text}}{{/if}}\n{{#if key}}Key: {{key}}{{/if}}\n{{#if modifiers}}Modifiers: {{modifiers}}{{/if}}\n\nProvide confirmation."
    depends_on: [type_text]
    inputs: [type_text.output, press_key.output]
---

# Keyboard Control

Type text and execute keyboard shortcuts on macOS.

## Usage

Type text:
```
action=type
text=Hello World
```

Press a key:
```
action=key
key=return
```

Command+C (copy):
```
action=shortcut
key=c
modifiers=cmd
```

Command+Shift+S:
```
action=shortcut
key=S
modifiers=cmd,shift
```

## Actions

- **type**: Type a string of text
- **key**: Press a named key (return, space, delete, enter, etc.)
- **shortcut**: Press key with modifiers
- **hotkey**: Execute a key code directly

## Common Keys

- return, enter, space, delete
- tab, escape, escape
- up, down, left, right
- f1-f12, home, end, pageup, pagedown

## Modifiers

- **cmd** or **command**: Command (⌘)
- **shift**: Shift (⇧)
- **opt** or **option**: Option (⌥)
- **ctrl**: Control (⌃)

## Examples

### Type text
```
action=type
text=Hello World
```

### Press Enter
```
action=key
key=return
```

### Copy (Cmd+C)
```
action=shortcut
key=c
modifiers=cmd
```

### Save As (Cmd+Shift+S)
```
action=shortcut
key=S
modifiers=cmd,shift
```

### Close window (Cmd+W)
```
action=shortcut
key=w
modifiers=cmd
```