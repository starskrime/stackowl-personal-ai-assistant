---
name: window_minimize_all
description: Minimize, maximize, cascade, tile, arrange all windows for desktop organization
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🗂️"
  os: [darwin]
parameters:
  action:
    type: string
    description: "Action: minimize_all, maximize_all, cascade, tile, arrange"
    default: "minimize_all"
required: []
steps:
  - id: minimize_all
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"System Events\" to keystroke \"m\" using command down' && echo 'Minimized active window'"
      mode: "local"
    timeout_ms: 5000
  - id: minimize_all_apps
    tool: ShellTool
    args:
      command: "for app in $(osascript -e 'tell application \"System Events\" to get name of processes where visible is true'); do osascript -e \"tell application \\\"$app\\\" to minimize\" 2>/dev/null; done && echo 'Minimized all windows'"
      mode: "local"
    timeout_ms: 30000
  - id: maximize_window
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"System Events\" to click button 2 of window 1' 2>/dev/null && echo 'Maximized window'"
      mode: "local"
    timeout_ms: 5000
  - id: fullscreen
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"System Events\" to keystroke \"f\" using {command down, control down}' && echo 'Fullscreen mode'"
      mode: "local"
    timeout_ms: 5000
  - id: cascade_windows
    tool: ShellTool
    args:
      command: "echo 'Cascading windows...' && osascript -e 'tell application \"System Events\" to keystroke \"m\" using {command down, option down}'"
      mode: "local"
    timeout_ms: 10000
  - id: show_desktop
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"Finder\" to activate' -e 'tell application \"System Events\" to keystroke \"d\" using {command down, shift down}' && echo 'Showed desktop'"
      mode: "local"
    timeout_ms: 5000
  - id: mission_control
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"System Events\" to key code 126 using {control down}' && echo 'Mission Control opened'"
      mode: "local"
    timeout_ms: 5000
  - id: app_expose
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"System Events\" to key code 160' && echo 'App Expose activated'"
      mode: "local"
    timeout_ms: 5000
  - id: analyze
    type: llm
    prompt: "Window organization: '{{action}}'\n\nCompleted."
    depends_on: [minimize_all]
    inputs: [minimize_all.output]
---

# Window Minimize All

Organize and manage all desktop windows.

## Usage

Minimize current window:
```
/window_minimize_all
```

Show desktop:
```
action=show_desktop
```

Mission Control:
```
action=mission_control
```

## Actions

- **minimize_all**: Minimize active window
- **minimize_apps**: Minimize all app windows
- **maximize**: Maximize active window
- **fullscreen**: Toggle fullscreen
- **cascade**: Cascade windows
- **arrange**: Auto-arrange windows
- **show_desktop**: Show desktop (⌘⇧D)
- **mission_control**: Open Mission Control

## Examples

### Minimize all
```
action=minimize_apps
```

### Show desktop
```
action=show_desktop
```

### Open Mission Control
```
action=mission_control
```

## Keyboard Shortcuts

| Action | Keys |
|--------|------|
| Minimize | ⌘M |
| Maximize | Green button |
| Fullscreen | ⌃⌘F |
| Show Desktop | ⌘⇧D |
| Mission Control | F3 / ^↑ |

## Notes

- Some actions require clicking
- Mission Control key varies by Mac