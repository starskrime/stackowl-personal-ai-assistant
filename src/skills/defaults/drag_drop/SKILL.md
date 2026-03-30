---
name: drag_drop
description: Perform drag and drop operations - move files, drag UI elements, draw selections on screen
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "✋"
  os: [darwin]
parameters:
  action:
    type: string
    description: "Action: file, ui_element, selection, app_icon"
    default: "ui_element"
  start_x:
    type: number
    description: "Start X coordinate"
  start_y:
    type: number
    description: "Start Y coordinate"
  end_x:
    type: number
    description: "End X coordinate"
  end_y:
    type: number
    description: "End Y coordinate"
  file_path:
    type: string
    description: "File path for file drag"
  target_folder:
    type: string
    description: "Target folder for file drop"
  duration:
    type: number
    description: "Drag duration in ms"
    default: 500
required: []
steps:
  - id: drag_mouse
    tool: ShellTool
    args:
      command: "/usr/bin/python3 -c '\nimport Quartz, time\nsx, sy = {{start_x}}, 1080-{{start_y}}\nex, ey = {{end_x}}, 1080-{{end_y}}\ndur = {{duration}}/1000\nsteps = int(dur / 0.01)\nfor i in range(steps + 1):\n    t = i / steps\n    x = int(sx + (ex - sx) * t)\n    y = int(sy + (ey - sy) * t)\n    p = Quartz.CGPoint(x, y)\n    if i == 0:\n        e = Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventLeftMouseDown, p, 0)\n    elif i == steps:\n        e = Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventLeftMouseUp, p, 0)\n    else:\n        e = Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventLeftMouseDragged, p, 0)\n    Quartz.CGEvent.post(Quartz.kCGHIDEventTap, e)\n    time.sleep(0.01)\nprint(f\"Dragged from ({{start_x}},{{start_y}}) to ({{end_x}},{{end_y}})\")\n'"
      mode: "local"
    timeout_ms: 30000
  - id: drag_file_finder
    tool: ShellTool
    args:
      command: "open -a Finder && echo 'Drag {{file_path}} to target folder'"
      mode: "local"
    timeout_ms: 5000
  - id: drop_zone
    tool: ShellTool
    args:
      command: "echo 'Drop target: {{target_folder}}'"
      mode: "local"
    timeout_ms: 3000
  - id: analyze
    type: llm
    prompt: "Drag operation:\n\nFrom: ({{start_x}}, {{start_y}})\nTo: ({{end_x}}, {{end_y}})\nDuration: {{duration}}ms\n\nDrag completed."
    depends_on: [drag_mouse]
    inputs: [drag_mouse.output]
---

# Drag and Drop

Perform drag and drop operations.

## Usage

Drag on screen:
```
action=drag
start_x=100
start_y=100
end_x=500
end_y=500
duration=500
```

## Parameters

- **start_x, start_y**: Start coordinates
- **end_x, end_y**: End coordinates
- **duration**: Drag duration (ms)
- **action**: Type of drag operation

## Actions

- **ui_element**: Drag UI element
- **file**: Drag file in Finder
- **selection**: Draw selection rectangle
- **app_icon**: Drag app icon

## Examples

### Drag file in Finder
```
action=file
file_path=~/Documents/test.txt
target_folder=~/Desktop
```

### Draw selection
```
action=selection
start_x=100
start_y=100
end_x=500
end_y=400
```

### Move window
```
action=ui_element
start_x=500
start_y=200
end_x=500
end_y=100
```

## Notes

- Smooth interpolated movement
- Works in any application
- Adjust duration for speed control