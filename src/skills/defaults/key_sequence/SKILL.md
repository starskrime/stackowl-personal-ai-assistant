---
name: key_sequence
description: Type key sequences, key combinations, and special characters with precise timing control
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "⌨️"
  os: [darwin]
parameters:
  keys:
    type: string
    description: "Key sequence to type (e.g., hello, world, or [cmd]v[cmd])"
  delay:
    type: number
    description: "Delay between keys in ms"
    default: 50
  modifiers:
    type: string
    description: "Modifiers: cmd, shift, opt, ctrl"
    default: ""
required: [keys]
steps:
  - id: type_text_fast
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"System Events\" to keystroke \"{{keys}}\"'"
      mode: "local"
    timeout_ms: 5000
  - id: type_with_delay
    tool: ShellTool
    args:
      command: "/usr/bin/python3 -c 'import Quartz; keys=\"{{keys}}\"; [Quartz.CGEvent.post(Quartz.kCGHIDEventTap, Quartz.CGEventCreateKeyboardEvent(None, ord(k), True)) or Quartz.CGEvent.post(Quartz.kCGHIDEventTap, Quartz.CGEventCreateKeyboardEvent(None, ord(k), False)) or __import__(\"time\").sleep({{delay}}/1000) for k in keys]'"
      mode: "local"
    timeout_ms: 30000
  - id: type_unicode
    tool: ShellTool
    args:
      command: "/usr/bin/python3 -c 'import Quartz; chars = [ord(c) for c in \"{{keys}}\"]; [Quartz.CGEvent.post(Quartz.kCGHIDEventTap, Quartz.CGEventCreateKeyboardEvent(None, c, True)) and Quartz.CGEvent.post(Quartz.kCGHIDEventTap, Quartz.CGEventCreateKeyboardEvent(None, c, False)) for c in chars]'"
      mode: "local"
    timeout_ms: 10000
  - id: key_with_modifier
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"System Events\" to keystroke \"{{keys}}\" using {{modifiers}} down'"
      mode: "local"
    timeout_ms: 5000
  - id: clear_field
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"System Events\" to keystroke \"a\" using command down' -e 'tell application \"System Events\" to keystroke (ASCII character 127)'"
      mode: "local"
    timeout_ms: 5000
  - id: analyze
    type: llm
    prompt: "Key sequence typed: '{{keys}}'\n\nDelay: {{delay}}ms\nModifiers: {{modifiers}}"
    depends_on: [type_text_fast]
    inputs: [type_text_fast.output]
---

# Key Sequence

Type key sequences with precise control.

## Usage

Type text:
```
keys=Hello World
```

Type with delay:
```
keys=Typing slowly...
delay=200
```

Type with modifier:
```
keys=v
modifiers=cmd
```

## Parameters

- **keys**: Text or keys to type
- **delay**: Delay between keystrokes (ms)
- **modifiers**: cmd, shift, opt, ctrl

## Examples

### Type email
```
keys=hello@example.com
```

### Select all and copy
```
keys=a
modifiers=cmd
---
keys=c
modifiers=cmd
```

### Type emoji
```
keys=😀
```

## Special Keys

- `[cmd]` - Command key
- `[shift]` - Shift key
- `[opt]` - Option key
- `[ctrl]` - Control key
- `[return]` - Return key
- `[tab]` - Tab key
- `[delete]` - Delete key