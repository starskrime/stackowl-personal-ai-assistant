---
name: port_scanner
description: Scan for open network ports on localhost or a specified host to check service availability
openclaw:
  emoji: "🔌"
---
# Port Scanner
Scan for open network ports.
## Steps
1. **Scan common ports:**
   ```bash
   run_shell_command("for port in 22 80 443 3000 5432 6379 8080 8443 27017; do (echo >/dev/tcp/<host>/$port) 2>/dev/null && echo \"Port $port: OPEN\" || echo \"Port $port: closed\"; done")
   ```
2. **Or scan a range:**
   ```bash
   run_shell_command("nc -zv <host> <start_port>-<end_port> 2>&1 | grep succeeded")
   ```
3. **Present results** showing open ports and likely services.
## Examples
### Scan localhost
```bash
run_shell_command("lsof -i -P | grep LISTEN")
```
## Error Handling
- **Permission denied:** Some ports require elevated privileges.
- **Host unreachable:** Check network connectivity first.
