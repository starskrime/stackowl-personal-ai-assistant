---
name: text_transform
description: Transform text with advanced operations like base64 encode/decode, URL encode, hash, sort, reverse lines and more
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🔡"
parameters:
  action:
    type: string
    description: "Action: encode64, decode64, url_encode, url_decode, hash, sort, reverse, count, word_count, line_count, trim, uniq"
    default: "read"
  content:
    type: string
    description: "Text content to transform"
  input_file:
    type: string
    description: "Input file path (alternative to content)"
  algorithm:
    type: string
    description: "Hash algorithm: md5, sha256, sha512 (for hash action)"
    default: "sha256"
required: []
steps:
  - id: read_input
    tool: ShellTool
    args:
      command: "cat {{input_file}} 2>/dev/null || echo '{{content}}'"
      mode: "local"
    timeout_ms: 5000
  - id: encode_base64
    tool: ShellTool
    args:
      command: "echo '{{content}}' | base64"
      mode: "local"
    timeout_ms: 5000
  - id: decode_base64
    tool: ShellTool
    args:
      command: "echo '{{content}}' | base64 -d"
      mode: "local"
    timeout_ms: 5000
  - id: url_encode_text
    tool: ShellTool
    args:
      command: "python3 -c \"import urllib.parse; print(urllib.parse.quote('{{content}}'))\""
      mode: "local"
    timeout_ms: 5000
  - id: url_decode_text
    tool: ShellTool
    args:
      command: "python3 -c \"import urllib.parse; print(urllib.parse.unquote('{{content}}'))\""
      mode: "local"
    timeout_ms: 5000
  - id: hash_text
    tool: ShellTool
    args:
      command: "echo -n '{{content}}' | {{#if_eq algorithm 'md5'}}md5{{/if_eq}}{{#if_eq algorithm 'sha256'}}shasum -a 256{{/if_eq}}{{#if_eq algorithm 'sha512'}}shasum -a 512{{/if_eq}}"
      mode: "local"
    timeout_ms: 5000
  - id: sort_text
    tool: ShellTool
    args:
      command: "echo '{{content}}' | sort"
      mode: "local"
    timeout_ms: 5000
  - id: reverse_text
    tool: ShellTool
    args:
      command: "echo '{{content}}' | rev"
      mode: "local"
    timeout_ms: 5000
  - id: count_chars
    tool: ShellTool
    args:
      command: "echo '{{content}}' | wc -c"
      mode: "local"
    timeout_ms: 5000
  - id: count_words
    tool: ShellTool
    args:
      command: "echo '{{content}}' | wc -w"
      mode: "local"
    timeout_ms: 5000
  - id: count_lines
    tool: ShellTool
    args:
      command: "echo '{{content}}' | wc -l"
      mode: "local"
    timeout_ms: 5000
  - id: trim_whitespace
    tool: ShellTool
    args:
      command: "echo '{{content}}' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//'"
      mode: "local"
    timeout_ms: 5000
  - id: unique_lines
    tool: ShellTool
    args:
      command: "echo '{{content}}' | sort | uniq"
      mode: "local"
    timeout_ms: 5000
  - id: analyze
    type: llm
    prompt: "Text transform: '{{action}}'\n\nInput: {{content}}\n\nResult:\n{{#if_eq action 'encode64'}}{{encode_base64.output}}{{/if_eq}}\n{{#if_eq action 'decode64'}}{{decode_base64.output}}{{/if_eq}}\n{{#if_eq action 'url_encode'}}{{url_encode_text.output}}{{/if_eq}}\n{{#if_eq action 'url_decode'}}{{url_decode_text.output}}{{/if_eq}}\n{{#if_eq action 'hash'}}{{hash_text.output}}{{/if_eq}}\n{{#if_eq action 'sort'}}{{sort_text.output}}{{/if_eq}}\n{{#if_eq action 'reverse'}}{{reverse_text.output}}{{/if_eq}}\n{{#if_eq action 'count'}}{{count_chars.output}} characters{{/if_eq}}\n{{#if_eq action 'word_count'}}{{count_words.output}} words{{/if_eq}}\n{{#if_eq action 'line_count'}}{{count_lines.output}} lines{{/if_eq}}\n{{#if_eq action 'trim'}}{{trim_whitespace.output}}{{/if_eq}}\n{{#if_eq action 'uniq'}}{{unique_lines.output}}{{/if_eq}}"
    depends_on: [read_input]
    inputs: [read_input.output, encode_base64.output, decode_base64.output]
---

# Text Transform

Advanced text transformations and encoding operations.

## Usage

URL encode:
```
action=url_encode
content=Hello World!
```

Base64 encode:
```
action=encode64
content=Hello
```

Hash text:
```
action=hash
content=password
algorithm=sha256
```

Sort lines:
```
action=sort
content=banana\napple\ncherry
```

## Actions

- **encode64**: Base64 encode
- **decode64**: Base64 decode
- **url_encode**: URL percent encoding
- **url_decode**: URL percent decoding
- **hash**: Calculate hash (md5, sha256, sha512)
- **sort**: Sort lines alphabetically
- **reverse**: Reverse characters or lines
- **count**: Count characters
- **word_count**: Count words
- **line_count**: Count lines
- **trim**: Remove leading/trailing whitespace
- **uniq**: Remove duplicate lines

## Examples

### URL encode
```
action=url_encode
content=https://example.com/?q=test 123
```

### SHA-256 hash
```
action=hash
content=mysecretpassword
algorithm=sha256
```

### Sort and unique
```
action=uniq
content=apple\nbanana\napple\ncherry
```

### Count file words
```
input_file=./README.md
action=word_count
```

## Notes

- Use `input_file` to read from file instead of `content`
- Newlines in content are expanded as literal \n
- Hash uses algorithm parameter (default: sha256)