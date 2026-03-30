---
name: app_switcher
description: Switch between applications instantly, show running apps, launch recent apps, and manage app windows
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🔄"
  os: [darwin]
parameters:
  action:
    type: string
    description: "Action: switch, list, launch, quit, hide"
    default: "switch"
  app_name:
    type: string
    description: "Application name"
required: []
steps:
  - id: list_running
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"System Events\" to get name of every process where visible is true' | tr ',' '\n'"
      mode: "local"
    timeout_ms: 10000
  - id: switch_to_app
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"{{app_name}}\" to activate' && echo 'Switched to {{app_name}}'"
      mode: "local"
    timeout_ms: 5000
  - id: quit_app
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"{{app_name}}\" to quit' && echo 'Quit {{app_name}}'"
      mode: "local"
    timeout_ms: 10000
  - id: hide_app
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"{{app_name}}\" to hide' && echo 'Hidden {{app_name}}'"
      mode: "local"
    timeout_ms: 5000
  - id: switch_with_cmd_tab
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"System Events\" to keystroke \"Tab\" using command down' && echo 'Switched app'"
      mode: "local"
    timeout_ms: 5000
  - id: force_quit_menu
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"System Events\" to keystroke \"Escape\" using {command down, option down, control down}' && echo 'Force Quit opened'"
      mode: "local"
    timeout_ms: 5000
  - id: app_front
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"System Events\" to keystroke \"h\" using command down' && echo 'Hidden front app (⌘H)'"
      mode: "local"
    timeout_ms: 5000
  - id: app_hide_others
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"System Events\" to keystroke \"h\" using {command down, option down}' && echo 'Hidden other apps (⌘⌥H)'"
      mode: "local"
    timeout_ms: 5000
  - id: analyze
    type: llm
    prompt: "App switcher action: '{{action}}'\n\nRunning apps:\n{{list_running.output}}"
    depends_on: [list_running]
    inputs: [list_running.output]
---

# App Switcher

Switch between and manage applications.

## Usage

List running apps:
```
/app_switcher
```

Switch to an app:
```
action=switch
app_name=Safari
```

Quit an app:
```
action=quit
app_name=Chrome
```

## Actions

- **switch**: Switch to app by name
- **list**: Show all running apps
- **launch**: Launch app (if not running)
- **quit**: Quit application
- **hide**: Hide application (⌘H)
- **hide_others**: Hide all other apps (⌘⌥H)

## Examples

### Switch to Safari
```
action=switch
app_name=Safari
```

### List apps
```
action=list
```

### Hide Chrome
```
action=hide
app_name=Google Chrome
```

### Quit Slack
```
action=quit
app_name=Slack
```

## Keyboard Shortcuts

| Action | Keys |
|--------|------|
| Switch App | ⌘Tab |
| Hide App | ⌘H |
| Hide Others | ⌘⌥H |
| Force Quit | ⌃⌥⌘Esc |
| Switch Again | ⌘⇧Tab |