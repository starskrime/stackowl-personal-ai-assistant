---
name: click_precise
description: Perform precise mouse clicks at exact screen coordinates with click types (single, double, right, triple)
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🎯"
  os: [darwin]
parameters:
  x:
    type: number
    description: "X coordinate (pixels from left)"
  y:
    type: number
    description: "Y coordinate (pixels from top)"
  click_type:
    type: string
    description: "Click type: single, double, right, triple, quad"
    default: "single"
  hold_time:
    type: number
    description: "Hold duration in ms for long press"
    default: 0
required: [x, y]
steps:
  - id: get_screen_size
    tool: ShellTool
    args:
      command: "system_profiler SPDisplaysDataType 2>/dev/null | grep Resolution | head -1"
      mode: "local"
    timeout_ms: 5000
  - id: move_to_position
    tool: ShellTool
    args:
      command: "/usr/bin/python3 -c 'import Quartz; p=Quartz.CGPoint({{x}}, 1080-{{y}}); e=Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventMouseMoved, p, 0); Quartz.CGEvent.post(Quartz.kCGHIDEventTap, e)'"
      mode: "local"
    timeout_ms: 5000
  - id: single_click
    tool: ShellTool
    args:
      command: "/usr/bin/python3 -c 'import Quartz; p=Quartz.CGPoint({{x}}, 1080-{{y}}); e=Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventLeftMouseDown, p, 0); Quartz.CGEvent.post(Quartz.kCGHIDEventTap, e); e=Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventLeftMouseUp, p, 0); Quartz.CGEvent.post(Quartz.kCGHIDEventTap, e)'"
      mode: "local"
    timeout_ms: 5000
  - id: double_click
    tool: ShellTool
    args:
      command: "/usr/bin/python3 -c 'import Quartz; p=Quartz.CGPoint({{x}}, 1080-{{y}}); [Quartz.CGEvent.post(Quartz.kCGHIDEventTap, Quartz.CGEventCreateMouseEvent(None, t, p, 0)) for t in [Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp]*2]'"
      mode: "local"
    timeout_ms: 5000
  - id: triple_click
    tool: ShellTool
    args:
      command: "/usr/bin/python3 -c 'import Quartz; p=Quartz.CGPoint({{x}}, 1080-{{y}}); [Quartz.CGEvent.post(Quartz.kCGHIDEventTap, Quartz.CGEventCreateMouseEvent(None, t, p, 0)) for t in [Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp]*3]'"
      mode: "local"
    timeout_ms: 5000
  - id: right_click
    tool: ShellTool
    args:
      command: "/usr/bin/python3 -c 'import Quartz; p=Quartz.CGPoint({{x}}, 1080-{{y}}); e=Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventRightMouseDown, p, 0); Quartz.CGEvent.post(Quartz.kCGHIDEventTap, e); e=Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventRightMouseUp, p, 0); Quartz.CGEvent.post(Quartz.kCGHIDEventTap, e)'"
      mode: "local"
    timeout_ms: 5000
  - id: long_press
    tool: ShellTool
    args:
      command: "/usr/bin/python3 -c 'import Quartz, time; p=Quartz.CGPoint({{x}}, 1080-{{y}}); e=Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventLeftMouseDown, p, 0); Quartz.CGEvent.post(Quartz.kCGHIDEventTap, e); time.sleep({{hold_time}}/1000); e=Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventLeftMouseUp, p, 0); Quartz.CGEvent.post(Quartz.kCGHIDEventTap, e)'"
      mode: "local"
    timeout_ms: 10000
  - id: verify_position
    tool: ShellTool
    args:
      command: "echo 'Click at ({{x}}, {{y}}) - {{click_type}} click'"
      mode: "local"
    timeout_ms: 3000
  - id: analyze
    type: llm
    prompt: "Click action at ({{x}}, {{y}}):\n\nType: {{click_type}}\n{{#if hold_time}}Hold time: {{hold_time}}ms{{/if}}\n\nClick performed."
    depends_on: [verify_position]
    inputs: [verify_position.output]
---

# Click Precise

Perform precise mouse clicks at exact coordinates.

## Usage

Single click:
```
x=500
y=500
click_type=single
```

Double click:
```
x=300
y=200
click_type=double
```

Right click (context menu):
```
x=500
y=500
click_type=right
```

Triple click (select paragraph):
```
x=500
y=500
click_type=triple
```

Long press:
```
x=500
y=500
click_type=single
hold_time=500
```

## Parameters

- **x, y**: Exact screen coordinates
- **click_type**: single, double, right, triple, quad
- **hold_time**: Duration in ms for long press

## Examples

### Open context menu
```
x=100
y=100
click_type=right
```

### Select word (triple click)
```
x=300
y=400
click_type=triple
```

### Long press to drag
```
x=500
y=500
hold_time=1000
```

## Notes

- Y coordinate is inverted (0 = top)
- Works in any application
- Requires accessibility permission