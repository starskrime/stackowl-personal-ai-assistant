---
name: blog_writer
description: Draft a structured blog post with title, introduction, sections, conclusion, and SEO metadata
openclaw:
  emoji: "✏️"
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
web_search query="AI agents practical applications 2026"
write_file("~/Documents/blog/ai_agents_guide.md", "<drafted content>")
```

## Error Handling

- **No topic specified:** Ask user for the topic and target audience.
- **Research returns thin results:** Rely on existing knowledge and note sources.
