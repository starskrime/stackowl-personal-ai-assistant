---
name: goal_setter
description: Define personal or professional goals with milestones, deadlines, and progress tracking
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🎯"
parameters:
  title:
    type: string
    description: "Goal title"
  target_date:
    type: string
    description: "Target completion date"
  milestones:
    type: string
    description: "Comma-separated milestones"
required: [title]
steps:
  - id: init_file
    tool: ShellTool
    args:
      command: "test -f ~/stackowl_goals.md || echo '# Goals\n' > ~/stackowl_goals.md"
      mode: "local"
    timeout_ms: 5000
  - id: add_goal
    tool: WriteFileTool
    args:
      path: "~/stackowl_goals.md"
      content: "## 🎯 {{title}}\n\n**Target Date:** {{target_date}}\n**Status:** In Progress\n\n### Milestones\n\n- [ ] {{milestones}}\n\n### Progress Notes\n\n- $(date +%Y-%m-%d): Goal created\n"
---

# Goal Setter

Create and track goals with milestones stored in `~/stackowl_goals.md`.

## Usage

```bash
/goal_setter title=<title> target_date=<date> milestones=<milestones>
```

## Parameters

- **title**: Goal title
- **target_date**: Target completion date
- **milestones**: Comma-separated milestones

## Examples

### Set a learning goal

```
title=Learn Rust
target_date=2026-06-01
milestones="Complete Rust book, Build CLI tool, Contribute to open source"
```

## Error Handling

- **No deadline provided:** Suggest a reasonable timeline based on goal complexity.
- **File doesn't exist:** Create with header.
