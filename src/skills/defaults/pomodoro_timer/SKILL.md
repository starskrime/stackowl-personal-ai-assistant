---
name: pomodoro_timer
description: Start a Pomodoro focus timer with configurable work and break intervals and macOS notifications
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🍅"
  os: [darwin]
parameters:
  work_minutes:
    type: number
    description: "Work session duration in minutes"
    default: 25
  break_minutes:
    type: number
    description: "Break duration in minutes"
    default: 5
required: []
steps:
  - id: show_start_time
    tool: ShellTool
    args:
      command: "date -v+{{work_minutes}}M '+%H:%M'"
      mode: "local"
  - id: start_work_timer
    tool: ShellTool
    args:
      command: "(sleep {{work_minutes * 60}} && osascript -e 'display notification \"Work session complete! Time for a break.\" with title \"🍅 Pomodoro\" sound name \"Glass\"') &"
      mode: "local"
  - id: confirm_start
    type: llm
    prompt: "Confirm that the Pomodoro session has started. Work duration: {{work_minutes}} minutes. Break duration: {{break_minutes}} minutes. Session will end at {{show_start_time.output}}."
    depends_on: [start_work_timer]
    inputs: [work_minutes, break_minutes, show_start_time.output]
---

# Pomodoro Timer

Run a Pomodoro focus session with notifications.

## Usage

```bash
/pomodoro_timer
/pomodoro_timer work_minutes=50 break_minutes=10
```

## Parameters

- **work_minutes**: Work session duration in minutes (default: 25)
- **break_minutes**: Break duration in minutes (default: 5)

## Examples

### Standard 25/5 Pomodoro

```bash
(sleep 1500 && osascript -e 'display notification "Time for a break!" with title "🍅 Pomodoro" sound name "Glass"') &
```

### Long 50/10 session

```bash
(sleep 3000 && osascript -e 'display notification "Long session done!" with title "🍅 Pomodoro" sound name "Glass"') &
```

## Error Handling

- **osascript not available:** Use `say "Pomodoro complete"` as audio fallback.
- **User wants to cancel:** Kill background sleep: `pkill -f 'sleep.*Pomodoro'`.
