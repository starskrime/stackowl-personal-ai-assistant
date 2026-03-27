---
name: debug_error
description: Diagnose and fix runtime errors by analyzing stack traces, error messages, and source code context
openclaw:
  emoji: "🐛"
---

# Debug Error

Analyze error messages and stack traces to find root causes.

## Steps

1. **Collect the error information:**
   - Error message / stack trace from user
   - Or read from log file: `read_file("<log_path>")`

2. **Identify the error type:**
   - Syntax error → Show the problematic line
   - Runtime error → Trace the call stack
   - Import/module error → Check dependencies
   - Network error → Check connectivity

3. **Read the relevant source file:**

   ```bash
   read_file("<file_path>")
   ```

4. **Search for known solutions:**

   ```
   web_search query="<error_message> <language> fix"
   ```

5. **Propose a fix** with explanation and optionally apply it:
   ```bash
   write_file("<file_path>", "<fixed_content>")
   ```

## Examples

### Debug a Node.js error

```bash
read_file("src/index.ts")
web_search query="TypeError: Cannot read properties of undefined Node.js"
```

## Error Handling

- **Stack trace too long:** Focus on the first user-code frame (skip node_modules/library frames).
- **No source file accessible:** Ask user to share the relevant code.
- **Multiple possible causes:** List them ranked by likelihood.
