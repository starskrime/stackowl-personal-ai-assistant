---
name: manage_todo
description: Create, list, complete, and delete items in a local todo list stored as a markdown file
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "✅"
parameters:
  action:
    type: string
    description: "Action: add, complete, delete, or list"
    default: "list"
  task:
    type: string
    description: "Task description"
required: [action]
steps:
  - id: init_file
    tool: ShellTool
    args:
      command: "test -f ~/stackowl_todos.md || echo '# Todo List\n' > ~/stackowl_todos.md"
      mode: "local"
    timeout_ms: 5000
  - id: read_todos
    tool: ShellTool
    args:
      command: "cat ~/stackowl_todos.md"
      mode: "local"
    timeout_ms: 5000
  - id: add_task
    tool: ShellTool
    args:
      command: "echo '- [ ] {{task}}' >> ~/stackowl_todos.md"
      mode: "local"
    timeout_ms: 5000
    optional: true
  - id: complete_task
    tool: ShellTool
    args:
      command: "sed -i '' 's/- \\[ \\] {{task}}/- [x] {{task}}/' ~/stackowl_todos.md"
      mode: "local"
    timeout_ms: 5000
    optional: true
  - id: delete_task
    tool: ShellTool
    args:
      command: "sed -i '' '/{{task}}/d' ~/stackowl_todos.md"
      mode: "local"
    timeout_ms: 5000
    optional: true
---

# Manage Todo List

Manage a persistent todo list stored at `~/stackowl_todos.md`.

## Usage

```bash
/manage_todo action=<add|complete|delete|list> task=<task>
```

## Parameters

- **action**: Action: add, complete, delete, or list (default: list)
- **task**: Task description

## Examples

### Add a task

```
action=add
task=Buy groceries
```

### Complete a task

```
action=complete
task=Buy groceries
```

### Delete a task

```
action=delete
task=Buy groceries
```

## Error Handling

- **File doesn't exist:** Create it with a header: `echo '# Todo List\n' > ~/stackowl_todos.md`
- **Task not found for completion/deletion:** List all tasks and ask user to specify which one.
- **Duplicate task names:** Show line numbers and ask user to pick the correct one using `grep -n`.
