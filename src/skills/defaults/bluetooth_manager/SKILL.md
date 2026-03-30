---
name: bluetooth_manager
description: Check Bluetooth status, list paired devices, and toggle Bluetooth on or off on macOS
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🔵"
  os: [darwin]
parameters:
  action:
    type: string
    description: "Action: status, on, off, or list"
    default: "status"
steps:
  - id: check_blueutil
    tool: ShellTool
    args:
      command: "which blueutil || echo 'NOT_FOUND'"
      mode: "local"
    timeout_ms: 5000
  - id: install_blueutil
    tool: ShellTool
    args:
      command: "brew install blueutil"
      mode: "local"
    timeout_ms: 60000
    optional: true
  - id: bluetooth_status
    tool: ShellTool
    args:
      command: "blueutil --power"
      mode: "local"
    timeout_ms: 5000
  - id: bluetooth_on
    tool: ShellTool
    args:
      command: "blueutil --power 1"
      mode: "local"
    timeout_ms: 5000
  - id: bluetooth_off
    tool: ShellTool
    args:
      command: "blueutil --power 0"
      mode: "local"
    timeout_ms: 5000
  - id: list_devices
    tool: ShellTool
    args:
      command: "blueutil --paired --device-tree 2>/dev/null | head -30"
      mode: "local"
    timeout_ms: 10000
  - id: system_profiler
    tool: ShellTool
    args:
      command: "system_profiler SPBluetoothDataType 2>/dev/null | head -40"
      mode: "local"
    timeout_ms: 15000
    optional: true
  - id: analyze
    type: llm
    prompt: "Bluetooth manager result for action '{{action}}':\n\nBlueutil available: {{check_blueutil.output}}\nStatus: {{bluetooth_status.output}}\n\nPaired devices:\n{{list_devices.output}}\n\nProvide a clear summary of Bluetooth status."
    depends_on: [check_blueutil, bluetooth_status]
    inputs: [check_blueutil.output, bluetooth_status.output, list_devices.output]
---

# Bluetooth Manager

Manage Bluetooth on macOS - check status, toggle on/off, list devices.

## Usage

```bash
/bluetooth_manager
```

Toggle on:
```
action=on
```

Toggle off:
```
action=off
```

List devices:
```
action=list
```

## Actions

- **status** (default): Check if Bluetooth is on or off
- **on**: Turn Bluetooth on
- **off**: Turn Bluetooth off
- **list**: Show paired Bluetooth devices

## Examples

### Check Bluetooth status
```
action=status
```

### Turn Bluetooth on
```
action=on
```

### List paired devices
```
action=list
```

## Error Handling

- **blueutil not installed:** Auto-installs via Homebrew
- **Fallback:** Uses System Profiler if blueutil unavailable
- **No devices:** Reports when no paired devices found