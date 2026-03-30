---
name: translate_text
description: Translate text between languages using web-based translation services
command-dispatch: tool
command-tool: google_search
openclaw:
  emoji: "🌐"
parameters:
  text:
    type: string
    description: "Text to translate"
  source_language:
    type: string
    description: "Source language (use 'auto' for auto-detect)"
    default: "auto"
  target_language:
    type: string
    description: "Target language for translation"
required: [text, target_language]
steps:
  - id: translate
    tool: google_search
    args:
      query: "translate '{{text}}' from {{source_language}} to {{target_language}}"
    timeout_ms: 15000
  - id: present_result
    type: llm
    prompt: "Present the translation results clearly showing:\n- Original text: {{text}}\n- Translated text (from search results): {{translate.output}}\n- Source language: {{source_language}}\n- Target language: {{target_language}}\n\nIf the text is ambiguous, offer alternative translations."
    depends_on: [translate]
    inputs: [translate.output, text]
---

# Translate Text

Translate text between languages using web search for translation.

## Usage

```bash
/translate_text text="Hello world" target_language=Spanish
```

## Parameters

- **text**: Text to translate
- **source_language**: Source language (use 'auto' for auto-detect, default: auto)
- **target_language**: Target language for translation

## Examples

```
translate_text text="Good morning, how are you?" target_language=Spanish
translate_text text="你好世界" target_language=English source_language=auto
```

## Error Handling

- **Language not specified:** Ask user for target language, default source to "auto-detect."
- **Translation seems wrong:** Try an alternate query or web_crawl a dictionary site.
- **Very long text:** Split into paragraphs and translate sequentially.
