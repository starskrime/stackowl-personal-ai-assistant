---
name: window_manager
description: Manage macOS windows - arrange, resize, move, minimize, maximize, and focus windows
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🪟"
  os: [darwin]
parameters:
  action:
    type: string
    description: "Action: list, arrange, focus, minimize, maximize, close, or move"
    default: "list"
  app_name:
    type: string
    description: "Application name"
  x:
    type: number
    description: "X position"
    default: 0
  y:
    type: number
    description: "Y position"
    default: 0
  width:
    type: number
    description: "Window width"
    default: 800
  height:
    type: number
    description: "Window height"
    default: 600
required: []
steps:
  - id: list_windows
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"System Events\" to get name of every window of every process where visible is true' | tr ',' '\n' | head -30"
      mode: "local"
    timeout_ms: 10000
  - id: focus_app
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"{{app_name}}\" to activate'"
      mode: "local"
    timeout_ms: 5000
  - id: minimize_window
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"System Events\" to set miniaturized of (first window of process \"{{app_name}}\") to true'"
      mode: "local"
    timeout_ms: 5000
  - id: maximize_window
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"System Events\" to set bounds of (first window of process \"{{app_name}}\") to {0, 22, 1920, 1080}'"
      mode: "local"
    timeout_ms: 5000
  - id: close_window
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"{{app_name}}\" to close front window'"
      mode: "local"
    timeout_ms: 5000
  - id: move_window
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"System Events\" to set position of (first window of process \"{{app_name}}\") to {{{x}}, {{y}}}'"
      mode: "local"
    timeout_ms: 5000
  - id: resize_window
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"System Events\" to set size of (first window of process \"{{app_name}}\") to {{{width}}, {{height}}}'"
      mode: "local"
    timeout_ms: 5000
  - id: tile_left
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"System Events\" to set bounds of (first window of process \"{{app_name}}\") to {0, 22, 960, 1058}'"
      mode: "local"
    timeout_ms: 5000
  - id: tile_right
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"System Events\" to set bounds of (first window of process \"{{app_name}}\") to {960, 22, 1920, 1058}'"
      mode: "local"
    timeout_ms: 5000
  - id: get_window_bounds
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"System Events\" to get bounds of (first window of process \"{{app_name}}\")' 2>/dev/null || echo 'unknown'"
      mode: "local"
    timeout_ms: 5000
  - id: analyze
    type: llm
    prompt: "Window manager action: '{{action}}' on {{app_name}}\n\nOpen windows:\n{{list_windows.output}}\n\nCurrent bounds: {{get_window_bounds.output}}"
    depends_on: [list_windows]
    inputs: [list_windows.output, get_window_bounds.output]
---

# Window Manager

Manage macOS windows - arrange, resize, move, and focus.

## Usage

List open windows:
```
/window_manager
```

Focus an app:
```
action=focus
app_name=Safari
```

Move window:
```
action=move
app_name=Safari
x=100
y=100
```

Resize window:
```
action=resize
app_name=Safari
width=1200
height=800
```

Tile left (half screen):
```
action=tile_left
app_name=Safari
```

Tile right (half screen):
```
action=tile_right
app_name=Safari
```

Minimize window:
```
action=minimize
app_name=Safari
```

Maximize window:
```
action=maximize
app_name=Safari
```

Close window:
```
action=close
app_name=Safari
```

## Actions

- **list**: Show all open windows
- **focus**: Bring app to front
- **move**: Move window to position
- **resize**: Resize window
- **minimize**: Minimize to Dock
- **maximize**: Maximize to full screen
- **close**: Close front window
- **tile_left**: Tile to left half
- **tile_right**: Tile to right half

## Examples

### List windows
```
action=list
```

### Focus Safari
```
action=focus
app_name=Safari
```

### Move to corner
```
action=move
app_name=Terminal
x=0
y=0
```

## Notes

- Uses AppleScript and System Events
- Requires Accessibility permissions
- Some apps have non-standard windows