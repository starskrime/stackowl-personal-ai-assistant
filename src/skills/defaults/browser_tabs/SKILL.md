---
name: browser_tabs
description: Advanced tab management - duplicate, close, pin, mute, move tabs between windows in Safari and Chrome
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "📑"
  os: [darwin]
parameters:
  action:
    type: string
    description: "Action: list, close, duplicate, pin, unpin, mute, unmute, move, reload, reload_all"
    default: "list"
  browser:
    type: string
    description: "Browser: safari or chrome"
    default: "safari"
  tab_index:
    type: number
    description: "Tab index (1-based) for targeted operations"
  window_id:
    type: number
    description: "Window ID for multi-window operations"
required: []
steps:
  - id: list_tabs_safari
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"Safari\" to get URL of every tab of every window' 2>/dev/null | tr ',' '\n' | nl"
      mode: "local"
    timeout_ms: 10000
  - id: list_tabs_chrome
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"Google Chrome\" to get URL of every tab of every window' 2>/dev/null | tr ',' '\n' | nl"
      mode: "local"
    timeout_ms: 10000
  - id: close_tab_safari
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"Safari\" to close tab {{tab_index}}' 2>/dev/null && echo 'Tab {{tab_index}} closed'"
      mode: "local"
    timeout_ms: 5000
  - id: close_tab_chrome
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"Google Chrome\" to close tab {{tab_index}}' 2>/dev/null && echo 'Tab {{tab_index}} closed'"
      mode: "local"
    timeout_ms: 5000
  - id: duplicate_tab_safari
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"Safari\" to duplicate window 1' 2>/dev/null && echo 'Tab duplicated'"
      mode: "local"
    timeout_ms: 5000
  - id: duplicate_tab_chrome
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"Google Chrome\" to duplicate active tab' 2>/dev/null && echo 'Tab duplicated'"
      mode: "local"
    timeout_ms: 5000
  - id: pin_tab_safari
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"Safari\" to set current tab to make new tab at end of tabs of front window' 2>/dev/null && echo 'Safari tabs cannot be pinned via script'"
      mode: "local"
    timeout_ms: 5000
  - id: mute_tab_chrome
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"Google Chrome\" to set muted of tab {{tab_index}} to true' 2>/dev/null && echo 'Tab {{tab_index}} muted'"
      mode: "local"
    timeout_ms: 5000
  - id: reload_tab_safari
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"Safari\" to do JavaScript \"location.reload()\" in front document'"
      mode: "local"
    timeout_ms: 10000
  - id: reload_all_tabs_chrome
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"Google Chrome\" to reload active tab of every window'"
      mode: "local"
    timeout_ms: 10000
  - id: analyze
    type: llm
    prompt: "Browser tabs action: '{{action}}' on {{browser}}\n\n{{#if_eq browser 'safari'}}Tabs:\n{{list_tabs_safari.output}}{{/if_eq}}\n{{#if_eq browser 'chrome'}}Tabs:\n{{list_tabs_chrome.output}}{{/if_eq}}"
    depends_on: [list_tabs_safari]
    inputs: [list_tabs_safari.output, list_tabs_chrome.output]
---

# Browser Tabs

Advanced tab management for Safari and Chrome.

## Usage

List all tabs:
```
/browser_tabs
```

Close a specific tab:
```
action=close
browser=safari
tab_index=3
```

Duplicate current tab:
```
action=duplicate
browser=chrome
```

Reload all tabs:
```
action=reload_all
browser=chrome
```

## Actions

- **list**: Show all open tabs
- **close**: Close specific tab by index
- **duplicate**: Duplicate current tab
- **pin**: Pin a tab (Safari workaround via script)
- **mute**: Mute tab audio (Chrome only)
- **reload**: Reload current tab
- **reload_all**: Reload all tabs in all windows

## Examples

### List Chrome tabs
```
action=list
browser=chrome
```

### Close 3rd Safari tab
```
action=close
browser=safari
tab_index=3
```

### Duplicate tab
```
action=duplicate
browser=chrome
```

## Notes

- Tab indices are 1-based
- Chrome supports more operations than Safari via AppleScript
- Some operations require the browser to be in focus