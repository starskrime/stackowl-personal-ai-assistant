---
name: volume_control
description: Get or set the system volume level and toggle mute on macOS
openclaw:
  emoji: "🔊"
  os: [darwin]
---
# Volume Control
Adjust macOS system volume.
## Steps
1. **Get current volume:**
   ```bash
   run_shell_command("osascript -e 'output volume of (get volume settings)'")
   ```
2. **Set volume (0-100):**
   ```bash
   run_shell_command("osascript -e 'set volume output volume <level>'")
   ```
3. **Toggle mute:**
   ```bash
   run_shell_command("osascript -e 'set volume output muted true'")
   run_shell_command("osascript -e 'set volume output muted false'")
   ```
## Examples
### Set to 50%
```bash
run_shell_command("osascript -e 'set volume output volume 50'")
```
## Error Handling
- **Invalid value:** Clamp to 0-100 range.
