---
name: generate_outline
description: Create a detailed outline for documents, presentations, or books based on a topic and target audience
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🗂️"
parameters:
  topic:
    type: string
    description: "Topic for the outline"
  format:
    type: string
    description: "Format: article, presentation, or book"
    default: "article"
  audience:
    type: string
    description: "Target audience"
required: [topic]
steps:
  - id: research
    tool: duckduckgo_search
    args:
      query: "{{topic}} key subtopics"
      num: 5
  - id: generate_outline
    type: llm
    prompt: "Create a detailed hierarchical outline for a {{format}} about {{topic}} aimed at {{audience}}. Include introduction, main sections with subsections, and conclusion."
    depends_on: [research]
    inputs: [research.output]
---

# Generate Outline

Create structured outlines for documents.

## Usage

```bash
/generate_outline topic=<topic> format=<article|presentation|book> audience=<audience>
```

## Parameters

- **topic**: Topic for the outline
- **format**: Format: article, presentation, or book (default: article)
- **audience**: Target audience

## Examples

### Create article outline

```
topic=machine learning in healthcare
format=article
audience=software engineers
```

## Error Handling

- **Vague topic:** Ask clarifying questions about scope and audience.
