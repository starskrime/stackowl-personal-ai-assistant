---
name: json_formatter
description: Format, validate, and pretty-print JSON data from files, clipboard, or inline input
openclaw:
  emoji: "📐"
---
# JSON Formatter
Validate and format JSON data.
## Steps
1. **Get JSON input** from file, clipboard, or inline:
   - File: `read_file("<file.json>")`
   - Clipboard: `run_shell_command("pbpaste")`
2. **Validate and format:**
   ```bash
   run_shell_command("cat <file.json> | python3 -m json.tool")
   ```
3. **Save formatted output** if requested:
   ```bash
   run_shell_command("python3 -m json.tool < <file.json> > <file_formatted.json>")
   ```
## Examples
### Format a JSON file
```bash
run_shell_command("python3 -m json.tool < data.json")
```
### Validate clipboard JSON
```bash
run_shell_command("pbpaste | python3 -m json.tool")
```
## Error Handling
- **Invalid JSON:** Show the error line/position and suggest fixes.
- **Very large file:** Use streaming parser or process in chunks.
