---
name: flashcard_create
description: Create study flashcards from notes, documents, or topics and save them for spaced repetition review
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🃏"
parameters:
  topic:
    type: string
    description: "Topic name for the flashcard deck"
  source_file:
    type: string
    description: "Path to source file to create flashcards from"
  content:
    type: string
    description: "Content or notes to generate flashcards from"
required: [topic]
steps:
  - id: read_source
    tool: ReadFileTool
    args:
      path: "{{source_file}}"
    optional: true
  - id: create_directory
    tool: ShellTool
    args:
      command: "mkdir -p ~/flashcards"
      mode: "local"
    timeout_ms: 5000
  - id: generate_flashcards
    tool: WriteFileTool
    args:
      path: "~/flashcards/{{topic}}.md"
      content: "# {{topic}} Flashcards\n\n**Q:** <question>\n**A:** <answer>\n\n---\n"
---

# Flashcard Creator

Create study flashcards.

## Usage

```bash
/flashcard_create topic=<topic> source_file=<path> content=<notes>
```

## Parameters

- **topic**: Topic name for the flashcard deck
- **source_file**: Path to source file to create flashcards from
- **content**: Content or notes to generate flashcards from

## Examples

### Create from a topic

```
topic=javascript_basics
content="JavaScript closures, scope, hoisting"
```

### Create from a file

```
topic=python_functions
source_file=~/notes/python.txt
```

## Error Handling

- **Source too long:** Extract most important concepts only.
- **Directory doesn't exist:** Create with `mkdir -p ~/flashcards`.
