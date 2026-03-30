---
name: mouse_gesture
description: Record and playback mouse gesture sequences - draw patterns that trigger actions
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "✌️"
  os: [darwin]
parameters:
  action:
    type: string
    description: "Action: record, play, list"
    default: "list"
  gesture_name:
    type: string
    description: "Name for the gesture"
  gesture_path:
    type: string
    description: "JSON path of points: [[x1,y1],[x2,y2],...]"
  duration:
    type: number
    description: "Playback duration in seconds"
    default: 1
required: []
steps:
  - id: list_gestures
    tool: ShellTool
    args:
      command: "ls ~/Library/Application\ Support/StackOwl/gestures/ 2>/dev/null || echo 'No gestures found'"
      mode: "local"
    timeout_ms: 5000
  - id: save_gesture
    tool: ShellTool
    args:
      command: "mkdir -p ~/Library/Application\ Support/StackOwl/gestures && echo '{{gesture_path}}' > ~/Library/Application\ Support/StackOwl/gestures/{{gesture_name}}.json && echo 'Gesture saved: {{gesture_name}}'"
      mode: "local"
    timeout_ms: 5000
  - id: load_gesture
    tool: ShellTool
    args:
      command: "cat ~/Library/Application\ Support/StackOwl/gestures/{{gesture_name}}.json 2>/dev/null || echo '{}'"
      mode: "local"
    timeout_ms: 5000
  - id: playback_gesture
    tool: ShellTool
    args:
      command: "/usr/bin/python3 -c '\nimport Quartz, json, time\npoints = {{gesture_path}}\nsteps = max(1, len(points) // {{duration}})\nfor i in range(0, len(points), max(1, len(points)//50)):\n    x, y = points[i]\n    p = Quartz.CGPoint(x, 1080-y)\n    e = Quartz.CGEventCreateMouseEvent(None, Quartz.kCGEventMouseMoved, p, 0)\n    Quartz.CGEvent.post(Quartz.kCGHIDEventTap, e)\n    time.sleep(0.01)\nprint(f\"Played {len(points)} points\")\n'"
      mode: "local"
    timeout_ms: 30000
  - id: record_start
    tool: ShellTool
    args:
      command: "echo 'Recording gesture - use /mouse_position action=move to trace path, then save with /mouse_gesture action=save'"
      mode: "local"
    timeout_ms: 3000
  - id: delete_gesture
    tool: ShellTool
    args:
      command: "rm ~/Library/Application\ Support/StackOwl/gestures/{{gesture_name}}.json && echo 'Gesture deleted'"
      mode: "local"
    timeout_ms: 5000
  - id: analyze
    type: llm
    prompt: "Mouse gesture action: '{{action}}'\n\nGesture: {{gesture_name}}\n\n{{list_gestures.output}}"
    depends_on: [list_gestures]
    inputs: [list_gestures.output]
---

# Mouse Gesture

Record and playback mouse gesture patterns.

## Usage

List saved gestures:
```
/mouse_gesture
```

Save a gesture:
```
action=save
gesture_name=circle
gesture_path=[[500,300],[520,320],[540,350],[550,400]]
```

Play a gesture:
```
action=play
gesture_name=circle
duration=2
```

## Actions

- **list**: Show saved gestures
- **record**: Start recording mode
- **save**: Save gesture with name and path
- **play**: Play back a gesture
- **delete**: Remove a saved gesture

## Gesture Format

Gestures are JSON arrays of [x, y] coordinates:
```json
[[100,100], [200,200], [300,100], [200,100]]
```

## Examples

### Draw a line
```
action=save
gesture_name=hline
gesture_path=[[100,500],[700,500]]
```

### Play it back
```
action=play
gesture_name=hline
duration=1
```

## Use Cases

- Draw "L" to scroll to bottom
- Draw "C" to close window
- Draw patterns for quick actions