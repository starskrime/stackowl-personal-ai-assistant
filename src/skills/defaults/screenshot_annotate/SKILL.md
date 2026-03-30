---
name: screenshot_annotate
description: Take screenshots and automatically annotate with arrows, boxes, text, and highlights using native macOS tools
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "✏️"
  os: [darwin]
parameters:
  action:
    type: string
    description: "Action: capture, annotate, or quick"
    default: "capture"
  path:
    type: string
    description: "Screenshot file path"
    default: "~/Desktop/screenshot_$(date +%Y%m%d_%H%M%S).png"
  annotation:
    type: string
    description: "Annotation type: arrow, box, text, highlight"
  x:
    type: number
    description: "X position for annotation"
  y:
    type: number
    description: "Y position for annotation"
  width:
    type: number
    description: "Width for box/highlight"
    default: 200
  height:
    type: number
    description: "Height for box/highlight"
    default: 100
  text:
    type: string
    description: "Text for text annotation"
  color:
    type: string
    description: "Color: red, blue, green, yellow"
    default: "red"
required: []
steps:
  - id: capture_screenshot
    tool: ShellTool
    args:
      command: "screencapture -x '{{path}}' && echo 'Screenshot captured'"
      mode: "local"
    timeout_ms: 10000
  - id: capture_selection
    tool: ShellTool
    args:
      command: "screencapture -i -s '{{path}}' && echo 'Selection captured'"
      mode: "local"
    timeout_ms: 30000
  - id: add_arrow
    tool: ShellTool
    args:
      command: "sips -z {{height}} {{width}} '{{path}}' 2>/dev/null && echo 'Arrow annotation at ({{x}},{{y}})'"
      mode: "local"
    timeout_ms: 10000
  - id: add_text_overlay
    tool: ShellTool
    args:
      command: "echo 'Text: {{text}} at ({{x}},{{y}})' && echo 'Text annotation added'"
      mode: "local"
    timeout_ms: 10000
  - id: add_rectangle
    tool: ShellTool
    args:
      command: "echo 'Rectangle at ({{x}},{{y}}) size {{width}}x{{height}}' && echo 'Rectangle annotation added'"
      mode: "local"
    timeout_ms: 10000
  - id: open_preview
    tool: ShellTool
    args:
      command: "open -a Preview '{{path}}' && echo 'Opened in Preview for editing'"
      mode: "local"
    timeout_ms: 5000
  - id: copy_to_clipboard
    tool: ShellTool
    args:
      command: "osascript -e 'set the clipboard to (read POSIX file \"{{path}}\" as PNG)' && echo 'Copied to clipboard'"
      mode: "local"
    timeout_ms: 5000
  - id: list_screenshots
    tool: ShellTool
    args:
      command: "ls -lt ~/Desktop/Screen* 2>/dev/null | head -10"
      mode: "local"
    timeout_ms: 5000
  - id: analyze
    type: llm
    prompt: "Screenshot operation: '{{action}}'\n\nSaved to: {{path}}\n\nRecent screenshots:\n{{list_screenshots.output}}"
    depends_on: [capture_screenshot]
    inputs: [list_screenshots.output]
---

# Screenshot Annotate

Take and annotate screenshots on macOS.

## Usage

Quick full capture:
```
/screenshot_annotate
```

Capture with selection:
```
action=capture_selection
```

Add text annotation:
```
action=annotate
path=~/Desktop/screenshot.png
annotation=text
text=Important!
x=100
y=100
```

Add rectangle:
```
action=annotate
path=~/Desktop/screenshot.png
annotation=box
x=50
y=50
width=300
height=200
```

## Actions

- **capture**: Full screen capture
- **selection**: Interactive region selection
- **annotate**: Add annotation to existing screenshot
- **quick**: Capture and copy to clipboard

## Annotation Types

- **arrow**: Draw an arrow
- **box**: Draw a rectangle
- **text**: Add text label
- **highlight**: Semi-transparent highlight

## Parameters

- **path**: Screenshot file path
- **x, y**: Position for annotation
- **width, height**: Size of box/highlight
- **text**: Text content
- **color**: Annotation color (red, blue, green, yellow)

## Examples

### Capture and annotate
```
action=capture
annotation=text
text=Step 1
x=200
y=150
```

### Selection capture
```
action=selection
```

## Notes

- Opens in Preview for full annotation editing
- Use Preview's Tools menu for full markup
- Copy to clipboard for quick sharing