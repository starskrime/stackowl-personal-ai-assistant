---
name: summarize_document
description: Create a concise summary of a long document extracting key points, themes, and conclusions
openclaw:
  emoji: "📑"
---

# Summarize Document

Condense long documents into key takeaways.

## Steps

1. **Read the document:**
   ```bash
   read_file("<file_path>")
   ```
2. **Generate summary** with:
   - One-paragraph executive summary
   - 5-7 key points
   - Main conclusions
   - Recommended actions (if applicable)
3. **Present** at the user's requested length (brief, medium, detailed).

## Examples

### Summarize a PDF

```bash
run_shell_command("pdftotext report.pdf -")
```

## Error Handling

- **Very long document (>50 pages):** Process in sections, summarize each, then create meta-summary.
- **Binary file:** Convert first using appropriate tool.
