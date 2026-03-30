---
name: mouse_position
description: Get current mouse position, move cursor smoothly, track mouse path, and convert coordinates between screens
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "📍"
  os: [darwin]
parameters:
  action:
    type: string
    description: "Action: get, move, track, convert"
    default: "get"
  x:
    type: number
    description: "Target X coordinate"
  y:
    type: number
    description: "Target Y coordinate"
  screen:
    type: string
    description: "Screen name for coordinate conversion"
    default: "main"
required: []
steps:
  - id: get_current_pos
    tool: ShellTool
    args:
      command: "echo $(/usr/bin/python3 -c 'import Quartz; p=Quartz.NSEvent.mouseLocation(); print(int(p.x), int(1080-p.y))')"
      mode: "local"
    timeout_ms: 5000
  - id: get_pos_detailed
    tool: ShellTool
    args:
      command: "/usr/bin/python3 -c 'import Quartz; p=Quartz.NSEvent.mouseLocation(); print(f\"X: {int(p.x)}, Y: {int(1080-p.y)}\")'"
      mode: "local"
    timeout_ms: 5000
  - id: move_to_xy
    tool: ShellTool
    args:
      command: "/usr/bin/python3 -c 'import Quartz; p=Quartz.CGPoint({{x}}, 1080-{{y}}); e=Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventMouseMoved, p, 0); Quartz.CGEvent.post(Quartz.kCGHIDEventTap, e)' && echo 'Moved to ({{x}}, {{y}})'"
      mode: "local"
    timeout_ms: 5000
  - id: smooth_move
    tool: ShellTool
    args:
      command: "/usr/bin/python3 -c '\nimport Quartz\ncur_x, cur_y = {{x}} // 2, 540 - {{y}} // 2\ntgt_x, tgt_y = {{x}}, 1080 - {{y}}\nsteps = 10\nfor i in range(steps + 1):\n    x = int(cur_x + (tgt_x - cur_x) * i / steps)\n    y = int(cur_y + (tgt_y - cur_y) * i / steps)\n    p = Quartz.CGPoint(x, y)\n    e = Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventMouseMoved, p, 0)\n    Quartz.CGEvent.post(Quartz.kCGHIDEventTap, e)\n'"
      mode: "local"
    timeout_ms: 10000
  - id: track_position
    tool: ShellTool
    args:
      command: "/usr/bin/python3 -c 'import Quartz, time; positions=[]; [positions.append(str(Quartz.NSEvent.mouseLocation())) or time.sleep(0.1) for _ in range(10)]; print(\"\\n\".join(positions))'"
      mode: "local"
    timeout_ms: 15000
  - id: get_screens
    tool: ShellTool
    args:
      command: "/usr/bin/python3 -c 'import Quartz; screens=Quartz.CGScreenList(); [print(f\"Screen {i}: {s}\") for i, s in enumerate(screens)]'"
      mode: "local"
    timeout_ms: 5000
  - id: analyze
    type: llm
    prompt: "Mouse position:\n\nCurrent: {{get_pos_detailed.output}}\n\n{{#if x}}Target: ({{x}}, {{y}}){{/if}}"
    depends_on: [get_current_pos]
    inputs: [get_current_pos.output]
---

# Mouse Position

Get, move, and track mouse cursor position.

## Usage

Get current position:
```
/mouse_position
```

Move to coordinates:
```
action=move
x=1000
y=500
```

Smooth move:
```
action=move
x=500
y=500
smooth=true
```

Track mouse for 1 second:
```
action=track
```

## Actions

- **get**: Show current cursor position
- **move**: Move cursor to coordinates
- **track**: Track position for 1 second
- **convert**: Convert coordinates between screens

## Examples

### Where is the mouse?
```
action=get
```

### Move to center of screen
```
action=move
x=960
y=540
```

### Track movement
```
action=track
```

## Coordinate System

- X: 0 (left) to screen width (right)
- Y: 0 (top) to screen height (bottom)
- Note: macOS inverts Y (0 = top)