---
name: battery_status
description: Check battery charge level, health, cycle count, and power source on macOS laptops
openclaw:
  emoji: "🔋"
  os: [darwin]
---

# Battery Status

Display battery information on macOS.

## Steps

1. **Get battery info:**
   ```bash
   run_shell_command("pmset -g batt")
   run_shell_command("system_profiler SPPowerDataType 2>/dev/null | grep -E 'Charge|Cycle|Condition|Connected'")
   ```
2. **Present summary:** charge %, time remaining, cycle count, health condition.

## Examples

### Quick check

```bash
run_shell_command("pmset -g batt")
```

## Error Handling

- **Desktop Mac:** Report "No battery — running on AC power."
