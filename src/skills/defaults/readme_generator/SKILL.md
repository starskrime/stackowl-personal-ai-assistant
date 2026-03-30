---
name: readme_generator
description: Generate a professional README.md for a project by analyzing its structure, dependencies, and code
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "📖"
parameters:
  project_path:
    type: string
    description: "Path to the project directory"
    default: "."
  project_name:
    type: string
    description: "Project name (auto-detected if not provided)"
  force:
    type: boolean
    description: "Overwrite existing README if present"
    default: false
required: []
steps:
  - id: list_files
    tool: ShellTool
    args:
      command: "ls -la {{project_path}}"
      mode: "local"
  - id: detect_package
    tool: ShellTool
    args:
      command: "cat {{project_path}}/package.json 2>/dev/null || cat {{project_path}}/pyproject.toml 2>/dev/null || cat {{project_path}}/Cargo.toml 2>/dev/null || echo 'NO_PACKAGE_FILE'"
      mode: "local"
  - id: detect_structure
    tool: ShellTool
    args:
      command: "find {{project_path}} -maxdepth 2 -type f -name '*.js' -o -name '*.ts' -o -name '*.py' -o -name '*.rs' -o -name '*.go' 2>/dev/null | head -20"
      mode: "local"
  - id: check_readme_exists
    tool: ShellTool
    args:
      command: "test -f {{project_path}}/README.md && echo 'EXISTS' || echo 'NOT_EXISTS'"
      mode: "local"
  - id: generate_readme
    type: llm
    prompt: "Generate a professional README.md for a project with the following characteristics:\n\nProject path: {{project_path}}\nProject name: {{project_name || 'auto-detect from package file'}}\n\nFiles found: {{list_files.output}}\nPackage info: {{detect_package.output}}\nSource files: {{detect_structure.output}}\n\nCreate sections: Project name & description, Features, Installation, Usage, Configuration, Contributing, License.\n\nIf force=false and README exists, ask before overwriting."
    depends_on: [list_files, detect_package, detect_structure, check_readme_exists]
    inputs: [project_path, project_name, list_files.output, detect_package.output, detect_structure.output, check_readme_exists.output]
  - id: save_readme
    tool: WriteFileTool
    args:
      path: "{{project_path}}/README.md"
      content: "{{generate_readme.output}}"
    optional: true
    depends_on: [generate_readme]
---

# README Generator

Generate a comprehensive project README.

## Usage

```bash
/readme_generator project_path=./myproject
/readme_generator project_path=./myproject project_name="My Project" force=true
```

## Parameters

- **project_path**: Path to the project directory (default: .)
- **project_name**: Project name (auto-detected if not provided)
- **force**: Overwrite existing README if present (default: false)

## Examples

### Generate for a Node.js project

```bash
cat package.json
ls src/
```

## Error Handling

- **No package file:** Infer from file structure.
- **README already exists:** Ask before overwriting (unless force=true).
