---
name: brightness_control
description: Adjust the display brightness level on macOS
openclaw:
  emoji: "🔆"
  os: [darwin]
---

# Brightness Control

Adjust macOS display brightness.

## Steps

1. **Get current brightness:**
   ```bash
   run_shell_command("brightness -l 2>/dev/null | grep brightness")
   ```
2. **Set brightness (0.0-1.0):**
   ```bash
   run_shell_command("brightness <level>")
   ```

## Examples

### Set to 70%

```bash
run_shell_command("brightness 0.7")
```

## Error Handling

- **brightness tool not installed:** `brew install brightness`.
- **External monitor:** May not support software brightness control—inform user.
