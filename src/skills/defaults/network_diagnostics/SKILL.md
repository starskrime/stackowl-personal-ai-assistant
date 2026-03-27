---
name: network_diagnostics
description: Diagnose network connectivity issues by testing DNS, ping, traceroute, and internet speed
openclaw:
  emoji: "🌐"
---

# Network Diagnostics

Troubleshoot network connectivity.

## Steps

1. **Check internet connectivity:**
   ```bash
   run_shell_command("ping -c 3 8.8.8.8")
   ```
2. **Check DNS resolution:**
   ```bash
   run_shell_command("nslookup google.com")
   ```
3. **Trace route to destination:**
   ```bash
   run_shell_command("traceroute -m 15 google.com 2>&1 | head -20")
   ```
4. **Show local IP:**
   ```bash
   run_shell_command("ifconfig en0 | grep 'inet '")
   ```
5. **Show public IP:**
   ```bash
   run_shell_command("curl -s ifconfig.me")
   ```

## Examples

### Full diagnostic

```bash
run_shell_command("ping -c 3 8.8.8.8 && nslookup google.com && curl -s ifconfig.me")
```

## Error Handling

- **ping fails:** Network is down — check WiFi/ethernet connection.
- **DNS fails but ping works:** DNS issue — suggest changing to 8.8.8.8 or 1.1.1.1.
