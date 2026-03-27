---
name: readme_generator
description: Generate a professional README.md for a project by analyzing its structure, dependencies, and code
openclaw:
  emoji: "📖"
---

# README Generator

Generate a comprehensive project README.

## Steps

1. **Analyze project:**
   ```bash
   run_shell_command("ls -la")
   run_shell_command("cat package.json 2>/dev/null || cat pyproject.toml 2>/dev/null")
   ```
2. **Generate README sections:**
   - Project name & description
   - Features list
   - Installation instructions
   - Usage examples
   - Configuration
   - Contributing guidelines
   - License
3. **Save:**
   ```bash
   write_file("README.md", "<generated_readme>")
   ```

## Examples

### Generate for a Node.js project

```bash
run_shell_command("cat package.json")
run_shell_command("ls src/")
```

## Error Handling

- **No package file:** Infer from file structure.
- **README already exists:** Ask before overwriting.
