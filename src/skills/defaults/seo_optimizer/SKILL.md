---
name: seo_optimizer
description: Analyze and optimize web content for search engine rankings with keyword suggestions and meta tag improvements
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🔎"
parameters:
  file_path:
    type: string
    description: "Path to the content file to analyze"
  topic:
    type: string
    description: "Topic/keyword to optimize for"
    default: ""
required: [file_path]
steps:
  - id: read_content
    tool: ReadFileTool
    args:
      path: "{{file_path}}"
  - id: research_keywords
    tool: duckduckgo_search
    args:
      query: "{{topic || read_content.output.substring(0, 100)}} related keywords search volume"
      num: 5
    optional: true
  - id: analyze_seo
    type: llm
    prompt: "Analyze the content for SEO optimization opportunities:\n\nContent:\n{{read_content.output}}\n\nCheck for:\n- Title tag length (50-60 chars optimal)\n- Meta description (150-160 chars)\n- H1/H2 usage and hierarchy\n- Keyword density (1-2% optimal)\n- Internal/external links\n- Alt text on images\n- Content length\n- Readability score\n\nKeyword research:\n{{research_keywords.output}}\n\nProvide an SEO report with actionable improvements."
    depends_on: [read_content, research_keywords]
    inputs: [read_content.output, research_keywords.output]
---

# SEO Optimizer

Optimize content for search engines.

## Usage

```bash
/seo_optimizer file_path=./blog/my_article.md topic="TypeScript best practices"
```

## Parameters

- **file_path**: Path to the content file to analyze (required)
- **topic**: Topic/keyword to optimize for (default: auto-detect from content)

## Examples

### Analyze a blog post

```bash
read_file("blog/my_article.md")
```

## Error Handling

- **No keywords specified:** Suggest keywords based on content analysis.
