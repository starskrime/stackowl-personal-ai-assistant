---
name: code_review
description: Review code files or git diffs for bugs, security issues, performance problems, and style violations
openclaw:
  emoji: "🔍"
---

# Code Review

Analyze code for quality, bugs, security, and best practices.

## Steps

1. **Get the code to review:**
   - From a file: `read_file("<file_path>")`
   - From git diff: `run_shell_command("git diff HEAD~1")`
   - From staged changes: `run_shell_command("git diff --cached")`

2. **Analyze across dimensions:**
   - **Bugs:** Logic errors, off-by-one, null/undefined
   - **Security:** SQL injection, XSS, hardcoded secrets
   - **Performance:** N+1 queries, unnecessary loops, memory leaks
   - **Style:** Naming conventions, code organization, DRY violations
   - **Error handling:** Missing try/catch, swallowed exceptions

3. **Present findings** as a structured review:
   ```markdown
   ## Code Review: <file_or_commit>

   ### 🐛 Bugs (Critical)
   - Line 42: Potential null reference...

   ### 🔒 Security
   - Line 15: API key hardcoded...

   ### ⚡ Performance
   - Line 78: O(n²) loop could be O(n)...

   ### 💅 Style
   - Inconsistent naming convention...

   ### ✅ What's Good
   - Clean separation of concerns...
   ```

## Examples

### Review a specific file
```bash
read_file("src/auth/login.ts")
```

### Review last commit
```bash
run_shell_command("git diff HEAD~1")
```

## Error Handling

- **File not found:** Ask user for correct path, use `find` to search.
- **Binary file:** Skip and note "Cannot review binary files."
- **Very large diff (>500 lines):** Focus on the most critical files first.
