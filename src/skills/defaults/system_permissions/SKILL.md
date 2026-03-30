---
name: system_permissions
description: Check and manage macOS permissions for Accessibility, Screen Recording, Automation, and Full Disk Access
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🔐"
  os: [darwin]
parameters:
  action:
    type: string
    description: "Action: check, grant, or request"
    default: "check"
  permission_type:
    type: string
    description: "Type: accessibility, screen_recording, automation, full_disk, camera, microphone"
    default: "accessibility"
  app_name:
    type: string
    description: "App name to check/grant"
    default: "StackOwl"
required: []
steps:
  - id: check_accessibility
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"System Events\" to return processes' 2>&1 | head -5 || echo 'Accessibility not granted'"
      mode: "local"
    timeout_ms: 5000
  - id: check_tcc
    tool: ShellTool
    args:
      command: "tccutil getAccessibility 2>/dev/null | head -10 || echo 'Could not read TCC database'"
      mode: "local"
    timeout_ms: 5000
  - id: check_screen_recording
    tool: ShellTool
    args:
      command: "osascript -e 'tell application \"System Events\" to return has clipboard' 2>&1 || echo 'Screen recording may not be granted'"
      mode: "local"
    timeout_ms: 5000
  - id: open_privacy
    tool: ShellTool
    args:
      command: "open 'x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility' && echo 'Opened Accessibility settings'"
      mode: "local"
    timeout_ms: 5000
  - id: open_screen_recording
    tool: ShellTool
    args:
      command: "open 'x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture' && echo 'Opened Screen Recording settings'"
      mode: "local"
    timeout_ms: 5000
  - id: open_automation
    tool: ShellTool
    args:
      command: "open 'x-apple.systempreferences:com.apple.preference.security?Privacy_Automation' && echo 'Opened Automation settings'"
      mode: "local"
    timeout_ms: 5000
  - id: open_full_disk
    tool: ShellTool
    args:
      command: "open 'x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles' && echo 'Opened Full Disk Access settings'"
      mode: "local"
    timeout_ms: 5000
  - id: open_camera
    tool: ShellTool
    args:
      command: "open 'x-apple.systempreferences:com.apple.preference.security?Privacy_Camera' && echo 'Opened Camera settings'"
      mode: "local"
    timeout_ms: 5000
  - id: open_microphone
    tool: ShellTool
    args:
      command: "open 'x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone' && echo 'Opened Microphone settings'"
      mode: "local"
    timeout_ms: 5000
  - id: analyze
    type: llm
    prompt: "System permissions check:\n\nAccessibility: {{check_accessibility.output}}\nScreen Recording: {{check_screen_recording.output}}\n\nTo grant permissions, open System Settings for the specific privacy category."
    depends_on: [check_accessibility]
    inputs: [check_accessibility.output, check_screen_recording.output]
---

# System Permissions

Check and manage macOS permissions for apps.

## Usage

Check permissions:
```
/system_permissions
```

Open Accessibility settings:
```
action=request
permission_type=accessibility
```

Open Screen Recording settings:
```
action=request
permission_type=screen_recording
```

## Permission Types

- **accessibility**: Control other apps (Automation)
- **screen_recording**: Screen capture (Screencapture)
- **automation**: Control other apps via AppleScript
- **full_disk**: Access all files (System Files)
- **camera**: Camera access
- **microphone**: Microphone access

## Actions

- **check**: Check current permission status
- **request**: Open System Settings for that permission
- **grant**: Attempt to grant permission programmatically

## Examples

### Check Accessibility
```
action=check
permission_type=accessibility
```

### Request Screen Recording
```
action=request
permission_type=screen_recording
```

### Open Full Disk Access
```
action=request
permission_type=full_disk
```

## Notes

- Some permissions require user interaction in System Settings
- macOS will prompt for permission when needed
- Full Disk Access requires admin password