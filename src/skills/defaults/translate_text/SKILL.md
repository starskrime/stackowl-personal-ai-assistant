---
name: translate_text
description: Translate text between languages using web-based translation services
openclaw:
  emoji: "🌐"
---

# Translate Text

Translate text between languages using web search for translation.

## Steps

1. **Identify source and target languages** from user request.

2. **Perform translation using web search:**
   ```
   web_search query="translate '<text>' from <source_language> to <target_language>"
   ```

3. **Present the translation** with:
   - Original text
   - Translated text
   - Source and target language labels
   - Pronunciation guide (for non-Latin scripts)

4. **Offer alternative translations** if the text is ambiguous.

## Examples

### English to Spanish
```
web_search query="translate 'Good morning, how are you?' to Spanish"
```

### Detect language and translate
```
web_search query="translate '你好世界' to English"
```

## Error Handling

- **Language not specified:** Ask user for target language, default source to "auto-detect."
- **Translation seems wrong:** Try an alternate query or web_crawl a dictionary site.
- **Very long text:** Split into paragraphs and translate sequentially.
