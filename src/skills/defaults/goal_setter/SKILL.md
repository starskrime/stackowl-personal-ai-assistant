---
name: goal_setter
description: Define personal or professional goals with milestones, deadlines, and progress tracking
openclaw:
  emoji: "🎯"
---

# Goal Setter

Create and track goals with milestones stored in `~/stackowl_goals.md`.

## Steps

1. **Collect goal details:**
   - Goal title
   - Target date
   - Key milestones (3–5 checkpoints)

2. **Format the goal:**

   ```markdown
   ## 🎯 <Goal Title>

   **Target Date:** <date>
   **Status:** In Progress

   ### Milestones

   - [ ] <milestone 1> — by <date>
   - [ ] <milestone 2> — by <date>
   - [ ] <milestone 3> — by <date>

   ### Progress Notes

   - <date>: Goal created
   ```

3. **Append to goals file:**

   ```bash
   run_shell_command("cat >> ~/stackowl_goals.md << 'GOAL'\n<formatted goal>\nGOAL")
   ```

4. **Confirm** the goal was saved.

## Examples

### Set a learning goal

```bash
run_shell_command("echo '## 🎯 Learn Rust\n**Target:** June 2026\n- [ ] Complete Rust book\n- [ ] Build CLI tool\n- [ ] Contribute to open source' >> ~/stackowl_goals.md")
```

## Error Handling

- **No deadline provided:** Suggest a reasonable timeline based on goal complexity.
- **File doesn't exist:** Create with header.
