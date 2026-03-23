---
name: bluetooth_manager
description: Check Bluetooth status, list paired devices, and toggle Bluetooth on or off on macOS
openclaw:
  emoji: "🔵"
  os: [darwin]
---
# Bluetooth Manager
Manage Bluetooth on macOS.
## Steps
1. **Check Bluetooth status:**
   ```bash
   run_shell_command("system_profiler SPBluetoothDataType 2>/dev/null | head -20")
   ```
2. **Toggle Bluetooth:**
   ```bash
   run_shell_command("blueutil --power 1")  # on
   run_shell_command("blueutil --power 0")  # off
   ```
3. **List paired devices:**
   ```bash
   run_shell_command("blueutil --paired")
   ```
## Examples
### Check status
```bash
run_shell_command("blueutil --power")
```
## Error Handling
- **blueutil not installed:** Install via `brew install blueutil`.
- **Fall back** to System Profiler if blueutil unavailable.
