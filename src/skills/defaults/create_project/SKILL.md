---
name: create_project
description: Scaffold a new software project with proper directory structure, config files, and git initialization
openclaw:
  emoji: "🏗️"
---

# Create Project

Scaffold a new project with best-practice structure.

## Steps

1. **Determine project type** from user request:
   - Node.js/TypeScript
   - Python
   - Go
   - Static HTML/CSS/JS

2. **Create the directory structure:**
   ```bash
   run_shell_command("mkdir -p <project_name>/{src,tests,docs}")
   ```

3. **Initialize based on type:**

   **Node.js:**
   ```bash
   run_shell_command("cd <project_name> && npm init -y && npx tsc --init")
   ```

   **Python:**
   ```bash
   run_shell_command("cd <project_name> && python3 -m venv venv && echo 'pytest\nblack\nmypy' > requirements.txt")
   ```

4. **Create essential files:**
   - `.gitignore` (language-appropriate)
   - `README.md` with project name and setup instructions
   - Base config files

5. **Initialize git:**
   ```bash
   run_shell_command("cd <project_name> && git init && git add -A && git commit -m 'chore: initial project scaffold'")
   ```

## Examples

### Create a TypeScript project
```bash
run_shell_command("mkdir -p my-app/{src,tests,docs} && cd my-app && npm init -y")
```

## Error Handling

- **Directory already exists:** Warn user and ask to confirm overwrite.
- **npm/python not installed:** Check with `which npm` / `which python3` and guide installation.
