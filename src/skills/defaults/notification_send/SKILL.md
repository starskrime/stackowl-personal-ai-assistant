---
name: notification_send
description: Send native macOS notifications with title, body, and sound options
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🔔"
  os: [darwin]
parameters:
  title:
    type: string
    description: "Notification title"
    default: "StackOwl"
  body:
    type: string
    description: "Notification body text"
  sound:
    type: string
    description: "Sound: default, none, ping, alarm"
    default: "default"
  action:
    type: string
    description: "Action button label (optional)"
required: [body]
steps:
  - id: send_notification
    tool: ShellTool
    args:
      command: "osascript -e 'display notification \"{{body}}\" with title \"{{title}}\"{{#if_eq sound 'none'}}  without icon{{/if_eq}}{{#if_eq sound 'ping'}} with sound name \"Ping\"{{/if_eq}}{{#if_eq sound 'alarm'}} with sound name \"Alarm\"{{/if_eq}}'"
      mode: "local"
    timeout_ms: 5000
  - id: send_with_sound
    tool: ShellTool
    args:
      command: "osascript -e 'display notification \"{{body}}\" with title \"{{title}}\" sound name \"{{sound}}\"'"
      mode: "local"
    timeout_ms: 5000
  - id: terminal_banner
    tool: ShellTool
    args:
      command: "echo $'\e[1;34m{{title}}\e[0m: {{body}}'"
      mode: "local"
    timeout_ms: 3000
  - id: analyze
    type: llm
    prompt: "Notification sent:\n\nTitle: {{title}}\nBody: {{body}}\nSound: {{sound}}"
    depends_on: [send_notification]
    inputs: [send_notification.output]
---

# Send Notification

Send native macOS notifications.

## Usage

Simple notification:
```
body=Task completed!
```

With title and sound:
```
title=Reminder
body=Meeting in 5 minutes
sound=ping
```

## Parameters

- **title**: Notification title (default: StackOwl)
- **body**: Notification text
- **sound**: Sound name (default, none, ping, alarm)

## Sounds

- **default**: System default sound
- **none**: No sound
- **ping**: Short ping sound
- **alarm**: Alarm sound

## Examples

### Reminder
```
title=Reminder
body=Call mom back
sound=ping
```

### Alert
```
title=ERROR
body=Build failed!
sound=alarm
```

### Silent
```
title=Done
body=Download complete
sound=none
```

## Notes

- Notifications appear in Notification Center
- User must grant notification permissions
- Sound names vary by macOS version