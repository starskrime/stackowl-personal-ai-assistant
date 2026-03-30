---
name: scroll_smooth
description: Smooth scrolling and panning in any application with configurable speed, direction, and distance
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🖱️"
  os: [darwin]
parameters:
  direction:
    type: string
    description: "Direction: up, down, left, right"
    default: "down"
  amount:
    type: number
    description: "Scroll amount (pixels or clicks)"
    default: 300
  speed:
    type: string
    description: "Speed: slow, medium, fast"
    default: "medium"
  smooth:
    type: boolean
    description: "Use smooth scrolling animation"
    default: true
required: []
steps:
  - id: scroll_down
    tool: ShellTool
    args:
      command: "/usr/bin/python3 -c 'import Quartz; e=Quartz.CGEventCreateScrollWheelEvent2(None, Quartz.kCGScrollWheelEventId, 1, {{amount}}/3); Quartz.CGEvent.post(Quartz.kCGHIDEventTap, e)'"
      mode: "local"
    timeout_ms: 5000
  - id: scroll_up
    tool: ShellTool
    args:
      command: "/usr/bin/python3 -c 'import Quartz; e=Quartz.CGEventCreateScrollWheelEvent2(None, Quartz.kCGScrollWheelEventId, 1, -{{amount}}/3); Quartz.CGEvent.post(Quartz.kCGHIDEventTap, e)'"
      mode: "local"
    timeout_ms: 5000
  - id: scroll_left
    tool: ShellTool
    args:
      command: "/usr/bin/python3 -c 'import Quartz; e=Quartz.CGEventCreateScrollWheelEvent2(None, Quartz.kCGScrollWheelEventId, 2, 0, {{amount}}/3); Quartz.CGEvent.post(Quartz.kCGHIDEventTap, e)'"
      mode: "local"
    timeout_ms: 5000
  - id: scroll_right
    tool: ShellTool
    args:
      command: "/usr/bin/python3 -c 'import Quartz; e=Quartz.CGEventCreateScrollWheelEvent2(None, Quartz.kCGScrollWheelEventId, 2, 0, -{{amount}}/3); Quartz.CGEvent.post(Quartz.kCGHIDEventTap, e)'"
      mode: "local"
    timeout_ms: 5000
  - id: smooth_scroll_slow
    tool: ShellTool
    args:
      command: "for i in $(seq 1 10); do /usr/bin/python3 -c 'import Quartz; e=Quartz.CGEventCreateScrollWheelEvent2(None, Quartz.kCGScrollWheelEventId, 1, {{amount}}/30); Quartz.CGEvent.post(Quartz.kCGHIDEventTap, e)'; done && echo 'Smooth scroll completed'"
      mode: "local"
    timeout_ms: 15000
  - id: smooth_scroll_fast
    tool: ShellTool
    args:
      command: "for i in $(seq 1 3); do /usr/bin/python3 -c 'import Quartz; e=Quartz.CGEventCreateScrollWheelEvent2(None, Quartz.kCGScrollWheelEventId, 1, {{amount}}/3); Quartz.CGEvent.post(Quartz.kCGHIDEventTap, e)'; done && echo 'Fast scroll completed'"
      mode: "local"
    timeout_ms: 10000
  - id: page_down
    tool: ShellTool
    args:
      command: "/usr/bin/python3 -c 'import Quartz; e=Quartz.CGEventCreateScrollWheelEvent2(None, Quartz.kCGScrollWheelEventId, 1, 15); Quartz.CGEvent.post(Quartz.kCGHIDEventTap, e)'"
      mode: "local"
    timeout_ms: 5000
  - id: analyze
    type: llm
    prompt: "Smooth scroll action:\n\nDirection: {{direction}}\nAmount: {{amount}}\nSpeed: {{speed}}\n\nScroll completed."
    depends_on: [scroll_down]
    inputs: [scroll_down.output]
---

# Smooth Scroll

Smooth scrolling and panning in any application.

## Usage

Scroll down:
```
direction=down
amount=300
```

Scroll up smoothly:
```
direction=up
amount=500
speed=slow
```

Page down:
```
direction=down
amount=15
```

## Parameters

- **direction**: up, down, left, right
- **amount**: Scroll amount (higher = more scrolling)
- **speed**: slow (gradual), medium, fast
- **smooth**: Animate the scroll

## Examples

### Gentle scroll
```
direction=down
amount=200
speed=slow
```

### Quick page
```
direction=down
amount=20
speed=fast
```

### Scroll left
```
direction=left
amount=400
```

## How It Works

- Uses macOS Quartz events for smooth scrolling
- Works in any application
- No clicks, pure gesture simulation