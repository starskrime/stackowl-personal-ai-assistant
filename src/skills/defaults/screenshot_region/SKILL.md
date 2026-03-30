---
name: screenshot_region
description: Take screenshots of a specific screen region, window, or the entire screen on macOS
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "📸"
  os: [darwin]
parameters:
  action:
    type: string
    description: "Action: full, region, window, or selection"
    default: "full"
  path:
    type: string
    description: "Output file path"
    default: "~/Desktop/screenshot_$(date +%Y%m%d_%H%M%S).png"
  x:
    type: number
    description: "X coordinate (for region)"
    default: 0
  y:
    type: number
    description: "Y coordinate (for region)"
    default: 0
  width:
    type: number
    description: "Width (for region)"
    default: 800
  height:
    type: number
    description: "Height (for region)"
    default: 600
  app_name:
    type: string
    description: "App name (for window capture)"
required: []
steps:
  - id: screenshot_full
    tool: ShellTool
    args:
      command: "screencapture '{{path}}' && echo 'Screenshot saved: {{path}}'"
      mode: "local"
    timeout_ms: 10000
  - id: screenshot_region
    tool: ShellTool
    args:
      command: "screencapture -x -R{{x}},{{y}},{{width}},{{height}} '{{path}}' && echo 'Region screenshot saved'"
      mode: "local"
    timeout_ms: 10000
  - id: screenshot_window
    tool: ShellTool
    args:
      command: "screencapture -x -w $(osascript -e 'tell application \"{{app_name}}\" to id of front window') '{{path}}' 2>/dev/null || screencapture -x -W '{{path}}' && echo 'Window screenshot saved'"
      mode: "local"
    timeout_ms: 10000
  - id: screenshot_selection
    tool: ShellTool
    args:
      command: "screencapture -i '{{path}}' && echo 'Interactive selection saved'"
      mode: "local"
    timeout_ms: 30000
  - id: screenshot_clipboard
    tool: ShellTool
    args:
      command: "screencapture -c && echo 'Screenshot copied to clipboard'"
      mode: "local"
    timeout_ms: 5000
  - id: list_screenshots
    tool: ShellTool
    args:
      command: "ls -lh ~/Desktop/Screen* 2>/dev/null | tail -10"
      mode: "local"
    timeout_ms: 5000
  - id: analyze
    type: llm
    prompt: "Screenshot action: '{{action}}'\n\n{{path}}\n\nRecent screenshots:\n{{list_screenshots.output}}"
    depends_on: [screenshot_full]
    inputs: [list_screenshots.output]
---

# Screenshot Region

Take screenshots of specific regions, windows, or the full screen.

## Usage

Full screen screenshot:
```
/screenshot_region
```

Region screenshot:
```
action=region
x=100
y=100
width=800
height=600
```

Capture specific window:
```
action=window
app_name=Safari
```

Interactive selection:
```
action=selection
```

Copy to clipboard:
```
action=clipboard
```

## Actions

- **full**: Capture entire screen
- **region**: Capture specific pixel region
- **window**: Capture specific application window
- **selection**: Interactive region selection (user draws)
- **clipboard**: Copy to clipboard instead of file

## Parameters

- **x, y**: Top-left corner of region
- **width, height**: Dimensions of region
- **path**: Output file path
- **app_name**: Target application for window capture

## Examples

### Capture region
```
action=region
x=0
y=0
width=1920
height=1080
```

### Safari window
```
action=window
app_name=Safari
```

### Custom path
```
path=~/Downloads/my_screenshot.png
action=full
```

## Defaults

- Output: ~/Desktop/screenshot_YYYYMMDD_HHMMSS.png
- Region: 800x600 starting at (0, 0)

## Notes

- `-x` flag disables shutter sound
- Use `-i` for interactive (selection) mode
- PNG format by default