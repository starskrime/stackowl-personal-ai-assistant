---
name: log_analyzer
description: Analyze log files to find errors, patterns, frequency distributions, and anomalies
openclaw:
  emoji: "🪵"
---
# Log Analyzer
Analyze log files for patterns and errors.
## Steps
1. **Read log file tail:**
   ```bash
   run_shell_command("tail -100 <logfile>")
   ```
2. **Count error types:**
   ```bash
   run_shell_command("grep -i 'error\|fatal\|exception' <logfile> | wc -l")
   run_shell_command("grep -i 'error' <logfile> | sort | uniq -c | sort -rn | head -10")
   ```
3. **Find time patterns:**
   ```bash
   run_shell_command("grep -i 'error' <logfile> | awk '{print $1, $2}' | cut -d: -f1-2 | uniq -c | sort -rn")
   ```
4. **Present summary:** error count, top errors, peak error times.
## Examples
### Analyze application log
```bash
run_shell_command("grep -c 'ERROR' /var/log/app.log && grep 'ERROR' /var/log/app.log | tail -5")
```
## Error Handling
- **File too large:** Use `tail -1000` or `grep` to filter relevant sections.
- **Permission denied:** Suggest using `sudo` or copying the file.
