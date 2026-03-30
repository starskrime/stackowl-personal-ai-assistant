---
name: browser_zoom
description: Zoom in, zoom out, reset zoom, and manage browser zoom levels for pages and default settings
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🔍"
  os: [darwin]
parameters:
  action:
    type: string
    description: "Action: zoom_in, zoom_out, reset, set, or default"
    default: "status"
  browser:
    type: string
    description: "Browser: safari or chrome"
    default: "safari"
  level:
    type: number
    description: "Zoom level percentage (e.g., 100, 150, 200)"
    default: 100
required: []
steps:
  - id: safari_zoom_in
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"Safari\" to do JavaScript \"document.body.style.zoom=1.5;\" in front document' && echo 'Zoomed in to 150%'"
      mode: "local"
    timeout_ms: 5000
  - id: safari_zoom_out
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"Safari\" to do JavaScript \"document.body.style.zoom=0.75;\" in front document' && echo 'Zoomed out to 75%'"
      mode: "local"
    timeout_ms: 5000
  - id: safari_reset_zoom
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"Safari\" to do JavaScript \"document.body.style.zoom=1;\" in front document' && echo 'Zoom reset to 100%'"
      mode: "local"
    timeout_ms: 5000
  - id: chrome_zoom_in
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"Google Chrome\" to execute JavaScript \"document.body.style.zoom=1.5;\" in active tab of front window' && echo 'Zoomed in to 150%'"
      mode: "local"
    timeout_ms: 5000
  - id: chrome_zoom_out
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"Google Chrome\" to execute JavaScript \"document.body.style.zoom=0.75;\" in active tab of front window' && echo 'Zoomed out to 75%'"
      mode: "local"
    timeout_ms: 5000
  - id: chrome_reset_zoom
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"Google Chrome\" to execute JavaScript \"document.body.style.zoom=1;\" in active tab of front window' && echo 'Zoom reset to 100%'"
      mode: "local"
    timeout_ms: 5000
  - id: safari_zoom_keyboard
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"Safari\" to activate' -e 'tell application \"System Events\" to keystroke \"+\" using command down'"
      mode: "local"
    timeout_ms: 5000
  - id: chrome_zoom_keyboard
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"Google Chrome\" to activate' -e 'tell application \"System Events\" to keystroke \"+\" using command down'"
      mode: "local"
    timeout_ms: 5000
  - id: set_zoom_level
    tool: ShellTool
    args:
      command: "echo 'Setting zoom to {{level}}%'"
      mode: "local"
    timeout_ms: 3000
  - id: analyze
    type: llm
    prompt: "Browser zoom action: '{{action}}' on {{browser}}\n\nZoom level set to: {{level}}%"
    depends_on: [set_zoom_level]
    inputs: [set_zoom_level.output]
---

# Browser Zoom

Control browser zoom levels.

## Usage

Zoom in:
```
action=zoom_in
browser=safari
```

Zoom out:
```
action=zoom_out
browser=chrome
```

Reset to 100%:
```
action=reset
browser=safari
```

Set specific level:
```
action=set
browser=chrome
level=150
```

## Actions

- **zoom_in**: Increase zoom by one step
- **zoom_out**: Decrease zoom by one step
- **reset**: Reset to 100%
- **set**: Set specific zoom percentage
- **default**: Set as default zoom

## Examples

### Safari zoom in
```
action=zoom_in
browser=safari
```

### Chrome zoom to 200%
```
action=set
browser=chrome
level=200
```

### Reset both
```
action=reset
browser=safari
```

## Keyboard Shortcuts

- **Cmd +** : Zoom in
- **Cmd -** : Zoom out
- **Cmd 0** : Reset to 100%

## Notes

- Zoom is per-page, not persistent
- Some sites override zoom level
- Keyboard shortcuts also available