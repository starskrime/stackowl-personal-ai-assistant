---
name: manage_todo
description: Create, list, complete, and delete items in a local todo list stored as a markdown file
openclaw:
  emoji: "✅"
---

# Manage Todo List

Manage a persistent todo list stored at `~/stackowl_todos.md`.

## Steps

1. **Read the current todo file:**

   ```bash
   run_shell_command("cat ~/stackowl_todos.md 2>/dev/null || echo '# Todo List'")
   ```

2. **Perform the requested action:**

   **Add a task:**

   ```bash
   run_shell_command("echo '- [ ] <task description>' >> ~/stackowl_todos.md")
   ```

   **Complete a task** (change `[ ]` to `[x]`):

   ```bash
   run_shell_command("sed -i '' 's/- \[ \] <task>/- [x] <task>/' ~/stackowl_todos.md")
   ```

   **Delete a task:**

   ```bash
   run_shell_command("sed -i '' '/<task>/d' ~/stackowl_todos.md")
   ```

   **List all tasks:**

   ```bash
   run_shell_command("cat ~/stackowl_todos.md")
   ```

3. **Show updated list** to the user after any modification.

## Examples

### Add a task

```bash
run_shell_command("echo '- [ ] Buy groceries' >> ~/stackowl_todos.md")
```

### Complete a task

```bash
run_shell_command("sed -i '' 's/- \[ \] Buy groceries/- [x] Buy groceries/' ~/stackowl_todos.md")
```

## Error Handling

- **File doesn't exist:** Create it with a header: `echo '# Todo List\n' > ~/stackowl_todos.md`
- **Task not found for completion/deletion:** List all tasks and ask user to specify which one.
- **Duplicate task names:** Show line numbers and ask user to pick the correct one using `grep -n`.
