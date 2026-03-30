---
name: brightness_control
description: Adjust the display brightness level on macOS
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🔆"
  os: [darwin]
parameters:
  level:
    type: number
    description: "Brightness level from 0.0 (darkest) to 1.0 (brightest). If not provided, shows current status."
required: []
steps:
  - id: check_brightness_tool
    tool: ShellTool
    args:
      command: "which brightness || echo 'NOT_FOUND'"
      mode: "local"
    timeout_ms: 5000
  - id: install_brightness
    tool: ShellTool
    args:
      command: "brew install brightness"
      mode: "local"
    timeout_ms: 60000
    optional: true
  - id: get_brightness
    tool: ShellTool
    args:
      command: "brightness -l 2>/dev/null | grep -E 'brightness|display' | head -5"
      mode: "local"
    timeout_ms: 5000
  - id: set_brightness
    tool: ShellTool
    args:
      command: "brightness {{level}}"
      mode: "local"
    timeout_ms: 5000
  - id: verify_brightness
    tool: ShellTool
    args:
      command: "brightness -l 2>/dev/null | grep brightness | head -1"
      mode: "local"
    timeout_ms: 5000
  - id: analyze
    type: llm
    prompt: "Brightness control result:\n\nCurrent status: {{get_brightness.output}}\n{{#if level}}New level set: {{level}}\nVerification: {{verify_brightness.output}}{{/if}}\n\nProvide a brief summary."
    depends_on: [get_brightness]
    inputs: [get_brightness.output, verify_brightness.output]
---

# Brightness Control

Adjust macOS display brightness.

## Usage

Show current brightness:
```
/brightness_control
```

Set to 70%:
```
level=0.7
```

Set to 50%:
```
level=0.5
```

## Examples

### Set to 70%
```
level=0.7
```

### Set to 50%
```
level=0.5
```

### Check current level
(No level parameter - just reads current value)

## Error Handling

- **brightness tool not installed:** Auto-installs via Homebrew
- **External monitor:** May not support software brightness control
- **Invalid level:** Must be between 0.0 and 1.0

## Notes

- Level is a float from 0.0 (darkest) to 1.0 (brightest)
- External monitors may not support this control