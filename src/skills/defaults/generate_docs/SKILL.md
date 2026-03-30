---
name: generate_docs
description: Generate API documentation or code documentation from source files with JSDoc or docstring analysis
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "📖"
parameters:
  source_file:
    type: string
    description: "Path to the source file to document"
  module_name:
    type: string
    description: "Name of the module for the output file"
required: [source_file]
steps:
  - id: read_source
    tool: ReadFileTool
    args:
      path: "{{source_file}}"
  - id: create_docs_directory
    tool: ShellTool
    args:
      command: "mkdir -p docs"
      mode: "local"
    timeout_ms: 5000
  - id: generate_docs
    tool: WriteFileTool
    args:
      path: "docs/{{module_name}}.md"
      content: "# {{module_name}}\n\n## Functions\n\n<!-- Add function documentation here -->\n"
---

# Generate Documentation

Create documentation from source code.

## Usage

```bash
/generate_docs source_file=<path> module_name=<name>
```

## Parameters

- **source_file**: Path to the source file to document
- **module_name**: Name of the module for the output file

## Examples

### Document a TypeScript module

```
source_file=src/skills/parser.ts
module_name=parser
```

## Error Handling

- **No type annotations:** Note "Types inferred from usage" and document best-effort.
- **Binary file:** Skip with "Cannot generate docs for binary files."
