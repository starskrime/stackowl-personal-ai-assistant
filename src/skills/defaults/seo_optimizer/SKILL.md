---
name: seo_optimizer
description: Analyze and optimize web content for search engine rankings with keyword suggestions and meta tag improvements
openclaw:
  emoji: "🔎"
---

# SEO Optimizer

Optimize content for search engines.

## Steps

1. **Read the content:**
   ```bash
   read_file("<file_path>")
   ```
2. **Analyze SEO factors:**
   - Title tag length (50-60 chars)
   - Meta description (150-160 chars)
   - H1/H2 usage
   - Keyword density
   - Internal/external links
   - Alt text on images
   - Content length
3. **Research keywords:**
   ```
   web_search query="<topic> related keywords search volume"
   ```
4. **Present SEO report** with actionable improvements.

## Examples

### Analyze a blog post

```bash
read_file("blog/my_article.md")
```

## Error Handling

- **No keywords specified:** Suggest keywords based on content analysis.
