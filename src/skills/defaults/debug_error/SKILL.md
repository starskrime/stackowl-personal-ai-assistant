---
name: debug_error
description: Diagnose and fix runtime errors by analyzing stack traces, error messages, and source code context
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🐛"
parameters:
  error_message:
    type: string
    description: "The error message or stack trace"
    default: ""
  file_path:
    type: string
    description: "Path to the source file with the error"
    default: ""
  language:
    type: string
    description: "Programming language (node, python, etc.)"
    default: ""
required: []
steps:
  - id: read_source_file
    tool: ReadFileTool
    args:
      path: "{{file_path}}"
    optional: true
  - id: search_fix
    tool: ShellTool
    args:
      command: "curl -s 'https://api.duckduckgo.com/?q={{error_message | urlencode}}+{{language}}+fix&format=json' 2>/dev/null | python3 -c \"import sys,json; d=json.load(sys.stdin); [print(r['Text']) for r in d.get('RelatedTopics',[])[:5]]\" 2>/dev/null || echo 'Search unavailable'"
      mode: "local"
    timeout_ms: 15000
    optional: true
  - id: analyze_error
    type: llm
    prompt: "Debug this error:\n\nError: {{error_message}}\n\nLanguage: {{language}}\n\nSource file {{file_path}}:\n{{read_source_file.output}}\n\nSearch results for fixes:\n{{search_fix.output}}\n\nIdentify:\n1. Error type (syntax, runtime, import, network, etc.)\n2. Root cause\n3. Recommended fix with code snippet\n4. Prevention tips"
    depends_on: [read_source_file, search_fix]
    inputs: [read_source_file.output, search_fix.stdout]
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

```
error_message="TypeError: Cannot read properties of undefined"
file_path="src/index.ts"
language="node"
```

## Error Handling

- **Stack trace too long:** Focus on the first user-code frame (skip node_modules/library frames).
- **No source file accessible:** Ask user to share the relevant code.
- **Multiple possible causes:** List them ranked by likelihood.
