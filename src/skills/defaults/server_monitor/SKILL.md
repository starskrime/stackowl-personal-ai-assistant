---
name: server_monitor
description: Monitor server health by checking HTTP endpoints, response times, and status codes
openclaw:
  emoji: "📡"
---
# Server Monitor
Check server/endpoint health.
## Steps
1. **Check HTTP endpoint:**
   ```bash
   run_shell_command("curl -s -o /dev/null -w 'Status: %{http_code}\nTime: %{time_total}s\nSize: %{size_download} bytes' <URL>")
   ```
2. **Check multiple endpoints** and present a status table.
3. **Alert** on non-200 responses or slow response times (>2s).
## Examples
### Check API health
```bash
run_shell_command("curl -s -o /dev/null -w '%{http_code} %{time_total}s' https://api.example.com/health")
```
## Error Handling
- **Connection refused:** Server is down or URL is wrong.
- **Timeout:** Increase with `--max-time 10`.
