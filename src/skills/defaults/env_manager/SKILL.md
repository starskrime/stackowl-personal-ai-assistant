---
name: env_manager
description: Manage environment variables by viewing, setting, and creating .env files for projects
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🌍"
parameters:
  action:
    type: string
    description: "Action: list, get, set, or file"
    default: "list"
  key:
    type: string
    description: "Environment variable name"
  value:
    type: string
    description: "Value to set (for set action)"
  file_path:
    type: string
    description: "Path to .env file (for file action)"
steps:
  - id: list_envs
    tool: ShellTool
    args:
      command: "env | sort"
      mode: "local"
    timeout_ms: 5000
  - id: get_env
    tool: ShellTool
    args:
      command: "echo \"{{key}}=${{key}}\""
      mode: "local"
    timeout_ms: 5000
  - id: set_env
    tool: ShellTool
    args:
      command: "export {{key}}='{{value}}' && echo \"{{key}} set to '{{value}}'\""
      mode: "local"
    timeout_ms: 5000
  - id: read_env_file
    tool: ReadFileTool
    args:
      path: "{{file_path}}"
  - id: write_env_file
    tool: WriteFileTool
    args:
      path: "{{file_path}}"
      content: "{{value}}"
  - id: check_gitignore
    tool: ShellTool
    args:
      command: "grep -q '.env' .gitignore 2>/dev/null && echo 'protected' || echo 'NOT_PROTECTED'"
      mode: "local"
    timeout_ms: 3000
  - id: analyze
    type: llm
    prompt: "Environment manager - action: '{{action}}'\n\n{{#if_eq action 'list'}}Environment variables:\n{{list_envs.output}}{{/if_eq}}\n{{#if_eq action 'get'}}Value of {{key}}:\n{{get_env.output}}{{/if_eq}}\n{{#if_eq action 'set'}}Result:\n{{set_env.output}}{{/if_eq}}\n{{#if_eq action 'file'}}File content:\n{{read_env_file.output}}\n\n.gitignore status: {{check_gitignore.output}}{{/if_eq}}"
    depends_on: [list_envs]
    inputs: [list_envs.output, get_env.output, set_env.output, read_env_file.output, check_gitignore.output]
---

# Environment Variable Manager

Manage environment variables and .env files.

## Usage

List all environment variables:
```
/env_manager
```

Get a specific variable:
```
action=get
key=HOME
```

Set a variable (temporary):
```
action=set
key=MY_VAR
value=my_value
```

Read a .env file:
```
action=file
file_path=.env
```

## Actions

- **list** (default): List all environment variables
- **get**: Get a specific variable's value
- **set**: Set a variable (temporary, session only)
- **file**: Read a .env file

## Examples

### List all vars
```
action=list
```

### Check API key
```
action=get
key=OPENAI_API_KEY
```

### Read project .env
```
action=file
file_path=./project/.env
```

## Security Notes

- **Never display full API keys** — show first/last 4 chars only
- **Check .gitignore** — ensure .env files are not committed
- **Use secret management** for production credentials