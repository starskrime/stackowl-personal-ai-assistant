---
name: mouse_control
description: Control mouse cursor movement, clicks, and drags on macOS using CLI tools
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🖱️"
  os: [darwin]
parameters:
  action:
    type: string
    description: "Action: move, click, double_click, right_click, drag, scroll"
    default: "click"
  x:
    type: number
    description: "X coordinate (for move/drag actions)"
  y:
    type: number
    description: "Y coordinate (for move/drag actions)"
  amount:
    type: number
    description: "Scroll amount (negative=down, positive=up)"
required: []
steps:
  - id: get_mouse_pos
    tool: ShellTool
    args:
      command: "echo $(/usr/bin/python3 -c 'import Quartz; p=Quartz.NSEvent.mouseLocation(); print(int(p.x), int(1080-p.y))')"
      mode: "local"
    timeout_ms: 5000
  - id: move_mouse
    tool: ShellTool
    args:
      command: "/usr/bin/python3 -c 'import Quartz; Quartz.CGEvent.post(Quartz.kCGHIDEventTap, Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventMouseMoved, Quartz.CGPoint({{x}}, 1080-{{y}}), 0))'"
      mode: "local"
    timeout_ms: 5000
  - id: left_click
    tool: ShellTool
    args:
      command: "/usr/bin/python3 -c 'import Quartz; e=Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventLeftMouseDown, Quartz.CGPoint({{x}}, 1080-{{y}}), 0); Quartz.CGEvent.post(Quartz.kCGHIDEventTap, e); e=Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventLeftMouseUp, Quartz.CGPoint({{x}}, 1080-{{y}}), 0); Quartz.CGEvent.post(Quartz.kCGHIDEventTap, e)'"
      mode: "local"
    timeout_ms: 5000
  - id: double_click
    tool: ShellTool
    args:
      command: "/usr/bin/python3 -c 'import Quartz; p=Quartz.CGPoint({{x}}, 1080-{{y}}); [Quartz.CGEvent.post(Quartz.kCGHIDEventTap, Quartz.CGEventCreateMouseEvent(None, t, p, 0)) for t in [Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp]*2]'"
      mode: "local"
    timeout_ms: 5000
  - id: right_click
    tool: ShellTool
    args:
      command: "/usr/bin/python3 -c 'import Quartz; e=Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventRightMouseDown, Quartz.CGPoint({{x}}, 1080-{{y}}), 0); Quartz.CGEvent.post(Quartz.kCGHIDEventTap, e); e=Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventRightMouseUp, Quartz.CGPoint({{x}}, 1080-{{y}}), 0); Quartz.CGEvent.post(Quartz.kCGHIDEventTap, e)'"
      mode: "local"
    timeout_ms: 5000
  - id: drag_mouse
    tool: ShellTool
    args:
      command: "/usr/bin/python3 -c 'import Quartz; p1=Quartz.CGPoint({{x}}, 1080-{{y}}); [Quartz.CGEvent.post(Quartz.kCGHIDEventTap, Quartz.CGEventCreateMouseEvent(None, t, p1, 0)) for t in [Quartz.kCGEventLeftMouseDown]]; [Quartz.CGEvent.post(Quartz.kCGHIDEventTap, Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventLeftMouseDragged, Quartz.CGPoint({{x}}, 1080-{{y}}), 0))]; Quartz.CGEvent.post(Quartz.kCGHIDEventTap, Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventLeftMouseUp, Quartz.CGPoint({{x}}, 1080-{{y}}), 0))'"
      mode: "local"
    timeout_ms: 5000
  - id: scroll
    tool: ShellTool
    args:
      command: "/usr/bin/python3 -c 'import Quartz; e=Quartz.CGEventCreateScrollWheelEvent2(None, Quartz.kCGScrollWheelEventId, 1, {{amount}}); Quartz.CGEvent.post(Quartz.kCGHIDEventTap, e)'"
      mode: "local"
    timeout_ms: 5000
  - id: analyze
    type: llm
    prompt: "Mouse control action: '{{action}}' at position ({{x}}, {{y}})\n\nCurrent mouse position: {{get_mouse_pos.output}}\n\nProvide confirmation of action performed."
    depends_on: [get_mouse_pos]
    inputs: [get_mouse_pos.output]
---

# Mouse Control

Control mouse cursor movement and clicks on macOS.

## Usage

Click at coordinates:
```
action=click
x=500
y=500
```

Move to position:
```
action=move
x=100
y=100
```

Double click:
```
action=double_click
x=500
y=500
```

Right click:
```
action=right_click
x=500
y=500
```

Scroll:
```
action=scroll
amount=-10
```

## Actions

- **move**: Move cursor to (x, y)
- **click**: Left click at (x, y)
- **double_click**: Double click at (x, y)
- **right_click**: Right click at (x, y)
- **drag**: Drag from current to (x, y)
- **scroll**: Scroll wheel (negative=down, positive=up)

## Notes

- Coordinates are screen pixels from top-left
- Y is inverted (0 = top of screen in most contexts)
- Requires accessibility permissions for some operations