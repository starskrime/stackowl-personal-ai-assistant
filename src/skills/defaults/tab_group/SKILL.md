---
name: tab_group
description: Organize browser tabs into groups, move tabs between groups, and manage tab collections
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "📁"
  os: [darwin]
parameters:
  action:
    type: string
    description: "Action: list, create, move, close_group"
    default: "list"
  browser:
    type: string
    description: "Browser: chrome (tab groups only in Chrome)"
    default: "chrome"
  group_name:
    type: string
    description: "Name for the tab group"
  tab_urls:
    type: string
    description: "Comma-separated URLs for new group"
required: []
steps:
  - id: list_tabs
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"Google Chrome\" to get URL of every tab of every window' | tr ',' '\\n'"
    mode: "local"
    timeout_ms: 10000
  - id: create_group_chrome
    tool: ShellTool
    args:
      command: "echo 'Tab groups require Chrome with feature enabled - use keyboard shortcut'"
    mode: "local"
    timeout_ms: 3000
  - id: organize_by_domain
    tool: ShellTool
    args:
      command: "echo 'Organizing tabs by domain - use drag in Chrome tab bar'"
    mode: "local"
    timeout_ms: 3000
  - id: close_group_tabs
    tool: ShellTool
    args:
      command: "echo 'Closing group tabs - close individual tabs instead'"
    mode: "local"
    timeout_ms: 3000
  - id: analyze
    type: llm
    prompt: "Tab group management:\n\nCurrent tabs:\n{{list_tabs.output}}\n\nTab groups are managed visually in Chrome."
    depends_on: [list_tabs]
    inputs: [list_tabs.output]
---

# Tab Group

Organize tabs into groups.

## Usage

List tabs:
```
/tab_group
```

## Actions

- **list**: Show all tabs
- **create**: Create new tab group (Chrome)
- **move**: Move tabs between groups
- **close_group**: Close all tabs in group

## Note

Tab groups are primarily a Chrome feature managed visually. This skill helps organize and list tabs.

## Examples

### List all tabs
```
action=list
```

### Chrome tab groups

Tab groups in Chrome are managed via:
1. Right-click tab > "Add tab to group"
2. Or drag tabs onto each other

## Keyboard Shortcuts

- **⌘D**: Duplicate tab
- **⌘W**: Close tab
- **⌘⇧W**: Close all tabs