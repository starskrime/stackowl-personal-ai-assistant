---
name: browser_navigate
description: Navigate web pages, fill forms, click elements, scroll, and interact with websites using AppleScript and CLI tools
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🧭"
  os: [darwin]
parameters:
  action:
    type: string
    description: "Action: navigate, click, fill, submit, scroll, back, forward, refresh"
    default: "navigate"
  browser:
    type: string
    description: "Browser: safari, chrome, firefox"
    default: "safari"
  url:
    type: string
    description: "URL to navigate to"
  element:
    type: string
    description: "Element selector or button name"
  value:
    type: string
    description: "Value to fill in form field"
  amount:
    type: number
    description: "Scroll amount in pixels"
required: []
steps:
  - id: navigate_safari
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"Safari\" to set URL of front document to \"{{url}}\"'"
      mode: "local"
    timeout_ms: 15000
  - id: navigate_chrome
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"Google Chrome\" to open location \"{{url}}\"'"
      mode: "local"
    timeout_ms: 15000
  - id: click_button_safari
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"Safari\" to do JavaScript \"document.querySelector('{{element}}').click()\" in front document'"
      mode: "local"
    timeout_ms: 10000
  - id: fill_form_safari
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"Safari\" to do JavaScript \"document.querySelector('{{element}}').value='{{value}}'\" in front document'"
      mode: "local"
    timeout_ms: 10000
  - id: scroll_safari
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"Safari\" to do JavaScript \"window.scrollBy(0, {{amount}})\" in front document'"
      mode: "local"
    timeout_ms: 10000
  - id: scroll_chrome
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"Google Chrome\" to execute JavaScript \"window.scrollBy(0, {{amount}})\" in active tab of front window'"
      mode: "local"
    timeout_ms: 10000
  - id: browser_back
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"{{browser}}\" to activate' -e 'tell application \"System Events\" to keystroke \"[\" using command down'"
      mode: "local"
    timeout_ms: 5000
  - id: browser_forward
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"{{browser}}\" to activate' -e 'tell application \"System Events\" to keystroke \"]\" using command down'"
      mode: "local"
    timeout_ms: 5000
  - id: browser_refresh
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"{{browser}}\" to activate' -e 'tell application \"System Events\" to keystroke \"r\" using command down'"
      mode: "local"
    timeout_ms: 5000
  - id: get_url
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"{{browser}}\" to return URL of front document' 2>/dev/null || echo 'unknown'"
      mode: "local"
    timeout_ms: 5000
  - id: analyze
    type: llm
    prompt: "Browser navigation: '{{action}}' on {{browser}}\n\nCurrent URL: {{get_url.output}}\n\nAction performed: {{action}}"
    depends_on: [get_url]
    inputs: [get_url.output]
---

# Browser Navigate

Navigate web pages, fill forms, click elements, and interact with websites.

## Usage

Navigate to URL:
```
action=navigate
browser=safari
url=https://example.com
```

Click an element:
```
action=click
browser=safari
element=button.submit
```

Fill a form field:
```
action=fill
browser=safari
element=input[name="email"]
value=test@example.com
```

Scroll:
```
action=scroll
amount=500
```

Go back/forward:
```
action=back
browser=safari
```

Refresh:
```
action=refresh
browser=safari
```

## Actions

- **navigate**: Go to URL
- **click**: Click element by CSS selector
- **fill**: Fill form field by selector
- **submit**: Submit a form
- **scroll**: Scroll by pixels (positive=down, negative=up)
- **back**: Go back in history
- **forward**: Go forward in history
- **refresh**: Reload page

## Examples

### Open website
```
action=navigate
browser=safari
url=https://github.com
```

### Click login button
```
action=click
element=button[type="submit"]
```

### Fill search box
```
action=fill
element=input[name="q"]
value=search term
```

### Scroll down
```
action=scroll
amount=300
```

## Notes

- Uses JavaScript injection for element interaction
- Works best with Safari and Chrome
- Some sites may block automation