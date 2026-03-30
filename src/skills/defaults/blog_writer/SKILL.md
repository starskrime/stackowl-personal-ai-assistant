---
name: blog_writer
description: Draft a structured blog post with title, introduction, sections, conclusion, and SEO metadata
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "✏️"
parameters:
  topic:
    type: string
    description: "The blog post topic"
  audience:
    type: string
    description: "Target audience (e.g., developers, executives, beginners)"
    default: "general"
  tone:
    type: string
    description: "Writing tone (professional, casual, technical)"
    default: "professional"
  key_points:
    type: string
    description: "Key points to cover (comma-separated)"
    default: ""
required: [topic]
steps:
  - id: research_topic
    tool: ShellTool
    args:
      command: "curl -s 'https://api.duckduckgo.com/?q={{topic}}+latest+insights+2026&format=json' 2>/dev/null | python3 -c \"import sys,json; d=json.load(sys.stdin); [print(r['Text']) for r in d.get('RelatedTopics',[])[:5]]\" 2>/dev/null || echo 'Research unavailable'"
      mode: "local"
    timeout_ms: 15000
    optional: true
  - id: write_blog
    type: llm
    prompt: "Write a blog post on '{{topic}}' for {{audience}} audience with {{tone}} tone. Key points to cover: {{key_points}}. Include: compelling title (60 chars max for SEO), hook introduction (2-3 sentences), 3-5 sections with headers, conclusion with CTA, and meta description (155 chars). Output the full blog post in markdown format."
    depends_on: [research_topic]
    inputs: [research_topic.stdout]
  - id: save_draft
    tool: WriteFileTool
    args:
      path: "~/Documents/blog/{{topic | slugify}}.md"
      content: "{{write_blog.output}}"
    optional: true
---

# Blog Writer

Draft structured blog posts.

## Steps

1. **Collect from user:** topic, target audience, tone, key points.
2. **Research the topic:**
   ```
   web_search query="<topic> latest insights 2026"
   ```
3. **Draft the blog post:**
   - Compelling title (60 chars max for SEO)
   - Hook introduction (2-3 sentences)
   - 3-5 sections with headers (H2)
   - Conclusion with CTA
   - Meta description (155 chars)
4. **Save the draft:**
   ```bash
   write_file("~/Documents/blog/<title_slug>.md", "<content>")
   ```

## Examples

### Draft a tech blog

```
topic="AI agents practical applications"
audience="developers"
tone="technical"
```

## Error Handling

- **No topic specified:** Ask user for the topic and target audience.
- **Research returns thin results:** Rely on existing knowledge and note sources.
