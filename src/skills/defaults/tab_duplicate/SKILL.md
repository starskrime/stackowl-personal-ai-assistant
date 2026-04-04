---
name: tab_duplicate
description: Duplicate the current browser tab or specific tabs in Safari and Chrome
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "📋"
  os: [darwin]
parameters:
  browser:
    type: string
    description: "Browser: safari or chrome"
    default: "safari"
  window_index:
    type: number
    description: "Window index (1-based)"
    default: 1
  tab_index:
    type: number
    description: "Tab index to duplicate (default: current)"
required: []
steps:
  - id: duplicate_safari
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"Safari\" to duplicate front window'"
    mode: "local"
    timeout_ms: 5000
  - id: duplicate_chrome
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"Google Chrome\" to duplicate active tab'"
    mode: "local"
    timeout_ms: 5000
  - id: get_current_url
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"{{browser}}\" to return URL of active tab of front window'"
    mode: "local"
    timeout_ms: 5000
  - id: open_duplicate
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"{{browser}}\" to open location \"{{get_current_url}}\"'"
    mode: "local"
    timeout_ms: 10000
  - id: analyze
    type: llm
    prompt: "Tab duplicate on {{browser}}:\n\nCurrent URL: {{get_current_url}}\n\nTab duplicated."
    depends_on: [get_current_url]
    inputs: [get_current_url.output]
---

# Tab Duplicate

Duplicate browser tabs.

## Usage

Duplicate current tab:
```
/tab_duplicate
```

Duplicate in Chrome:
```
browser=chrome
```

## Parameters

- **browser**: safari or chrome (default: safari)
- **window_index**: Window to target (default: 1)
- **tab_index**: Specific tab to duplicate

## Examples

### Safari
```
browser=safari
```

### Chrome
```
browser=chrome
```

## Notes

- Creates exact copy of current tab
- Works best in active browser window