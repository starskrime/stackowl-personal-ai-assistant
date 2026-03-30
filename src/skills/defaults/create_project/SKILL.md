---
name: create_project
description: Scaffold a new software project with proper directory structure, config files, and git initialization
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🏗️"
parameters:
  project_name:
    type: string
    description: "Name of the project to create"
  project_type:
    type: string
    description: "Project type (node, python, go, static)"
    default: "node"
required: [project_name]
steps:
  - id: create_dirs
    tool: ShellTool
    args:
      command: "mkdir -p {{project_name}}/src {{project_name}}/tests {{project_name}}/docs"
      mode: "local"
    timeout_ms: 5000
  - id: init_node
    tool: ShellTool
    args:
      command: "cd {{project_name}} && npm init -y 2>/dev/null && npx tsc --init 2>/dev/null || echo 'npm init skipped'"
      mode: "local"
    timeout_ms: 30000
    optional: true
  - id: init_python
    tool: ShellTool
    args:
      command: "cd {{project_name}} && python3 -m venv venv 2>/dev/null && echo -e 'pytest\\nblack\\nmypy' > requirements.txt || echo 'python init skipped'"
      mode: "local"
    timeout_ms: 30000
    optional: true
  - id: create_gitignore
    tool: WriteFileTool
    args:
      path: "{{project_name}}/.gitignore"
      content: "node_modules/\ndist/\n__pycache__/\n*.pyc\nvenv/\n.env\n.DS_Store\n"
    optional: true
  - id: create_readme
    tool: WriteFileTool
    args:
      path: "{{project_name}}/README.md"
      content: "# {{project_name}}\n\n## Setup\n\n## Usage\n"
    optional: true
  - id: init_git
    tool: ShellTool
    args:
      command: "cd {{project_name}} && git init && git add -A && git commit -m 'chore: initial project scaffold' || echo 'git init skipped'"
      mode: "local"
    timeout_ms: 10000
    optional: true
  - id: present_result
    type: llm
    prompt: "Summarize the created project structure for '{{project_name}}' ({{project_type}}). Show the directory layout and any initialization results."
    depends_on: [create_dirs, init_git]
    inputs: [create_dirs.stdout]
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
   mkdir -p <project_name>/{src,tests,docs}
   ```

3. **Initialize based on type:**

   **Node.js:**

   ```bash
   cd <project_name> && npm init -y && npx tsc --init
   ```

   **Python:**

   ```bash
   cd <project_name> && python3 -m venv venv && echo 'pytest\nblack\nmypy' > requirements.txt
   ```

4. **Create essential files:**
   - `.gitignore` (language-appropriate)
   - `README.md` with project name and setup instructions
   - Base config files

5. **Initialize git:**
   ```bash
   cd <project_name> && git init && git add -A && git commit -m 'chore: initial project scaffold'
   ```

## Examples

### Create a TypeScript project

```
project_name="my-app"
project_type="node"
```

## Error Handling

- **Directory already exists:** Warn user and ask to confirm overwrite.
- **npm/python not installed:** Check with `which npm` / `which python3` and guide installation.
