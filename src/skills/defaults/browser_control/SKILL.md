---
name: browser_control
description: Open browsers, manage tabs, take screenshots, and control browser actions on macOS
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🌐"
  os: [darwin]
parameters:
  action:
    type: string
    description: "Action: open, close, screenshot, tabs, bookmarks, or clear_cache"
    default: "open"
  browser:
    type: string
    description: "Browser: safari, chrome, firefox, edge"
    default: "safari"
  url:
    type: string
    description: "URL to open"
    default: "https://www.google.com"
required: []
steps:
  - id: open_safari
    tool: ShellTool
    args:
      command: "open -a Safari '{{url}}'"
      mode: "local"
    timeout_ms: 10000
  - id: open_chrome
    tool: ShellTool
    args:
      command: "open -a Google\ Chrome '{{url}}'"
      mode: "local"
    timeout_ms: 10000
  - id: open_firefox
    tool: ShellTool
    args:
      command: "open -a Firefox '{{url}}'"
      mode: "local"
    timeout_ms: 10000
  - id: open_edge
    tool: ShellTool
    args:
      command: "open -a Microsoft\ Edge '{{url}}'"
      mode: "local"
    timeout_ms: 10000
  - id: close_browser
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"{{browser}}\" to quit'"
      mode: "local"
    timeout_ms: 5000
  - id: safari_screenshot
    tool: ShellTool
    args:
      command: "screencapture -x ~/Desktop/safari_screenshot_$(date +%Y%m%d_%H%M%S).png && echo 'Screenshot saved to Desktop'"
      mode: "local"
    timeout_ms: 10000
  - id: list_tabs_safari
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"Safari\" to get URL of every tab of every window' 2>/dev/null | tr ',' '\n'"
      mode: "local"
    timeout_ms: 10000
  - id: list_tabs_chrome
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"Google Chrome\" to get URL of every tab of every window' 2>/dev/null | tr ',' '\n'"
      mode: "local"
    timeout_ms: 10000
  - id: clear_cache
    tool: ShellTool
    args:
      command: "rm -rf ~/Library/Caches/com.apple.Safari/* ~/Library/Caches/Google/Chrome/* ~/Library/Caches/Firefox/* 2>/dev/null && echo 'Browser cache cleared'"
      mode: "local"
    timeout_ms: 15000
  - id: analyze
    type: llm
    prompt: "Browser action: '{{action}}' on {{browser}}\n\n{{#if_eq action 'open'}}Opened {{url}} in {{browser}}{{/if_eq}}\n{{#if_eq action 'tabs'}}Open tabs:\n{{#if_eq browser 'safari'}}{{list_tabs_safari.output}}{{/if_eq}}\n{{#if_eq browser 'chrome'}}{{list_tabs_chrome.output}}{{/if_eq}}{{/if_eq}}\n{{#if_eq action 'screenshot'}}Screenshot taken{{/if_eq}}"
    depends_on: [open_safari]
    inputs: [list_tabs_safari.output, list_tabs_chrome.output]
---

# Browser Control

Open browsers, manage tabs, take screenshots, and control browser actions.

## Usage

Open URL in Safari:
```
action=open
browser=safari
url=https://example.com
```

Take screenshot:
```
action=screenshot
browser=safari
```

List open tabs:
```
action=tabs
browser=safari
```

Close browser:
```
action=close
browser=safari
```

Clear cache:
```
action=clear_cache
```

## Actions

- **open**: Open URL in specified browser
- **close**: Quit the browser
- **screenshot**: Capture screen (saves to Desktop)
- **tabs**: List all open tabs
- **bookmarks**: Show bookmarks (Safari only)
- **clear_cache**: Clear browser cache files

## Browsers

- **safari**: macOS default
- **chrome**: Google Chrome
- **firefox**: Mozilla Firefox
- **edge**: Microsoft Edge

## Examples

### Open Google in Chrome
```
action=open
browser=chrome
url=https://google.com
```

### Screenshot current screen
```
action=screenshot
```

### List Safari tabs
```
action=tabs
browser=safari
```