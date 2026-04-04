---
name: mouse_control
description: "Control mouse cursor movement, clicks, and drags on macOS. Actions: status, move, click, double_click, right_click, drag, scroll"
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🖱️"
  os: [darwin]
parameters:
  action:
    type: string
    description: "Action: status, move, click, double_click, right_click, drag, scroll"
    default: "status"
  x:
    type: number
    description: "X coordinate (pixels from left, default: current)"
    default: 0
  y:
    type: number
    description: "Y coordinate (pixels from top, default: current)"
    default: 0
  amount:
    type: number
    description: "Scroll amount (negative=down, positive=up, default: 3)"
    default: 3
required: []
steps:
  - id: get_mouse_pos
    tool: ShellTool
    args:
      command: "/usr/bin/python3 -c 'import Quartz; p=Quartz.NSEvent.mouseLocation(); print(int(p.x), int(1080-p.y))'"
    mode: "local"
    timeout_ms: 5000
  - id: do_status
    tool: ShellTool
    args:
      command: "echo 'Current mouse position: ({{get_mouse_pos}})'"
    mode: "local"
    timeout_ms: 3000
  - id: do_move
    tool: ShellTool
    args:
      command: "/usr/bin/python3 -c 'import Quartz; p=Quartz.CGPoint({{x}}, 1080-{{y}}); e=Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventMouseMoved, p, 0); Quartz.CGEvent.post(Quartz.kCGHIDEventTap, e)'"
    mode: "local"
    timeout_ms: 5000
  - id: do_click
    tool: ShellTool
    args:
      command: "/usr/bin/python3 -c 'import Quartz; p=Quartz.CGPoint({{x}}, 1080-{{y}}); e=Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventLeftMouseDown, p, 0); Quartz.CGEvent.post(Quartz.kCGHIDEventTap, e); e=Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventLeftMouseUp, p, 0); Quartz.CGEvent.post(Quartz.kCGHIDEventTap, e)'"
    mode: "local"
    timeout_ms: 5000
  - id: do_double_click
    tool: ShellTool
    args:
      command: "/usr/bin/python3 -c 'import Quartz; p=Quartz.CGPoint({{x}}, 1080-{{y}}); [Quartz.CGEvent.post(Quartz.kCGHIDEventTap, Quartz.CGEventCreateMouseEvent(None, t, p, 0)) for t in [Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp]*2]'"
    mode: "local"
    timeout_ms: 5000
  - id: do_right_click
    tool: ShellTool
    args:
      command: "/usr/bin/python3 -c 'import Quartz; p=Quartz.CGPoint({{x}}, 1080-{{y}}); e=Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventRightMouseDown, p, 0); Quartz.CGEvent.post(Quartz.kCGHIDEventTap, e); e=Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventRightMouseUp, p, 0); Quartz.CGEvent.post(Quartz.kCGHIDEventTap, e)'"
    mode: "local"
    timeout_ms: 5000
  - id: do_scroll
    tool: ShellTool
    args:
      command: "/usr/bin/python3 -c 'import Quartz; e=Quartz.CGEventCreateScrollWheelEvent2(None, Quartz.kCGScrollWheelEventId, 1, {{amount}}); Quartz.CGEvent.post(Quartz.kCGHIDEventTap, e)'"
    mode: "local"
    timeout_ms: 5000
  - id: analyze
    type: llm
    prompt: "Mouse action '{{action}}' completed. Position: {{get_mouse_pos}}"
    depends_on: [get_mouse_pos]
    inputs: [get_mouse_pos.output]
---

# Mouse Control

Control mouse cursor on macOS - move, click, scroll.

## Usage

Get current position (default):
```
/mouse_control
```

Move cursor:
```
action=move
x=500
y=300
```

Click at position:
```
action=click
x=500
y=300
```

Scroll down:
```
action=scroll
amount=5
```

## Parameters

- **action**: status (default), move, click, double_click, right_click, scroll
- **x**: X coordinate (default: current position)
- **y**: Y coordinate (default: current position)
- **amount**: Scroll amount, negative=down (default: 3)

## Examples

### Show mouse position
```
action=status
```

### Move to center
```
action=move
x=960
y=540
```

### Click at coordinates
```
action=click
x=100
y=100
```

### Scroll
```
action=scroll
amount=-5
```

## Notes

- Coordinates: X from left, Y from top (macOS inverts Y)
- Use mouse_position skill to find current coordinates first
- Requires Accessibility permission in System Settings