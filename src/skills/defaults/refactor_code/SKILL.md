---
name: refactor_code
description: Refactor code to improve readability, reduce duplication, and follow clean code principles
openclaw:
  emoji: "♻️"
---
# Refactor Code
Improve code quality through systematic refactoring.
## Steps
1. **Read the target file:**
   ```bash
   read_file("<file_path>")
   ```
2. **Identify refactoring opportunities:**
   - Duplicate code → Extract functions
   - Long functions → Split into smaller ones
   - Magic numbers → Extract constants
   - Deep nesting → Early returns / guard clauses
   - Dead code → Remove unused variables/functions
3. **Apply refactoring** and save:
   ```bash
   write_file("<file_path>", "<refactored_code>")
   ```
4. **Run tests** to verify behavior unchanged:
   ```bash
   run_shell_command("npm test")
   ```
## Examples
### Refactor a file
```bash
read_file("src/utils/helpers.ts")
```
## Error Handling
- **Tests fail after refactoring:** Revert changes and try smaller refactoring steps.
- **File too large:** Focus on one function/section at a time.
