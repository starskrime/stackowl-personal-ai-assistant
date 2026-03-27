---
name: wifi_manager
description: View current WiFi connection, scan available networks, and connect to a specified network on macOS
openclaw:
  emoji: "📶"
  os: [darwin]
---

# WiFi Manager

Manage WiFi connections on macOS.

## Steps

1. **Show current connection:**
   ```bash
   run_shell_command("networksetup -getairportnetwork en0")
   ```
2. **Scan available networks:**
   ```bash
   run_shell_command("/System/Library/PrivateFrameworks/Apple80211.framework/Versions/Current/Resources/airport -s")
   ```
3. **Connect to a network:**
   ```bash
   run_shell_command("networksetup -setairportnetwork en0 '<SSID>' '<password>'")
   ```
4. **Disconnect:**
   ```bash
   run_shell_command("networksetup -setairportpower en0 off && networksetup -setairportpower en0 on")
   ```

## Examples

### Check current WiFi

```bash
run_shell_command("networksetup -getairportnetwork en0")
```

## Error Handling

- **WiFi off:** Turn on with `networksetup -setairportpower en0 on`.
- **Wrong password:** Inform user and ask to retry.
- **No networks found:** Check if WiFi hardware is enabled.
