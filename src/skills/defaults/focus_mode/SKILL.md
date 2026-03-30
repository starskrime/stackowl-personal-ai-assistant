---
name: focus_mode
description: Enable system-wide focus mode on macOS by activating Do Not Disturb and optionally blocking distracting apps
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🧘"
  os: [darwin]
parameters:
  duration_seconds:
    type: number
    description: "Duration of focus session in seconds"
    default: 3600
  block_apps:
    type: string
    description: "Comma-separated list of app names to quit"
required: []
steps:
  - id: enable_dnd
    tool: ShellTool
    args:
      command: "shortcuts run 'Set Focus' 2>/dev/null || osascript -e 'do shell script \"defaults -currentHost write com.apple.notificationcenterui doNotDisturb -boolean true && killall NotificationCenter\"'"
      mode: "local"
    timeout_ms: 15000
  - id: block_apps
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"Slack\" to quit' && osascript -e 'tell application \"Discord\" to quit' && osascript -e 'tell application \"Twitter\" to quit'"
      mode: "local"
    timeout_ms: 10000
    optional: true
  - id: set_timer
    tool: ShellTool
    args:
      command: "(sleep {{duration_seconds}} && osascript -e 'display notification \"Focus session ended\" with title \"Focus Mode\"') &"
      mode: "local"
    timeout_ms: 5000
    optional: true
---

# Focus Mode

Enable macOS Do Not Disturb and optionally block distracting applications.

## Usage

```bash
/focus_mode duration_seconds=<seconds> block_apps=<Slack,Discord>
```

## Parameters

- **duration_seconds**: Duration of focus session in seconds (default: 3600)
- **block_apps**: Comma-separated list of app names to quit

## Examples

### 1-hour focus session

```
duration_seconds=3600
```

### Focus with app blocking

```
duration_seconds=7200
block_apps=Slack,Discord,Twitter,Messages
```

## Error Handling

- **DND command fails:** Inform user to enable Focus manually in System Settings.
- **App not running:** Silently skip—no error needed.
