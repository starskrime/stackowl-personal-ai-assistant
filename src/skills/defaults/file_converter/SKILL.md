---
name: file_converter
description: Convert files between formats including markdown to HTML, JSON to CSV, and document conversions
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🔄"
parameters:
  input:
    type: string
    description: "Input file path"
  output:
    type: string
    description: "Output file path"
  from_format:
    type: string
    description: "Source format: md, json, csv, yaml, txt, docx"
  to_format:
    type: string
    description: "Target format: html, json, csv, yaml, txt"
required: [input, output, from_format, to_format]
steps:
  - id: check_input
    tool: ShellTool
    args:
      command: "ls -la {{input}}"
      mode: "local"
    timeout_ms: 5000
  - id: convert_md_to_html
    tool: ShellTool
    args:
      command: "python3 -c \"import markdown; print(markdown.markdown(open('{{input}}').read()))\" > {{output}}"
      mode: "local"
    timeout_ms: 15000
    optional: true
  - id: convert_json_to_csv
    tool: ShellTool
    args:
      command: "python3 -c \"import json,csv,sys; data=json.load(open('{{input}}')); f=csv.writer(open('{{output}}','w')); f.writerow(data[0].keys() if isinstance(data,list) else data.keys()); [f.writerow(r.values() if isinstance(r,dict) else r) for r in (data if isinstance(data,list) else [data])]\""
      mode: "local"
    timeout_ms: 15000
    optional: true
  - id: convert_csv_to_json
    tool: ShellTool
    args:
      command: "python3 -c \"import csv,json; rows=list(csv.DictReader(open('{{input}}'))); json.dump(rows,open('{{output}}','w'),indent=2)\""
      mode: "local"
    timeout_ms: 15000
    optional: true
  - id: convert_yaml_to_json
    tool: ShellTool
    args:
      command: "python3 -c \"import yaml,json; json.dump(yaml.safe_load(open('{{input}}')),open('{{output}}','w'),indent=2)\""
      mode: "local"
    timeout_ms: 15000
    optional: true
  - id: convert_docx_to_txt
    tool: ShellTool
    args:
      command: "textutil -convert txt {{input}} -output {{output}}"
      mode: "local"
    timeout_ms: 15000
    optional: true
  - id: convert_any_to_txt
    tool: ShellTool
    args:
      command: "textutil -convert txt {{input}} -output {{output}}"
      mode: "local"
    timeout_ms: 15000
    optional: true
  - id: verify_output
    tool: ShellTool
    args:
      command: "ls -la {{output}} && head -10 {{output}}"
      mode: "local"
    timeout_ms: 5000
  - id: analyze
    type: llm
    prompt: "File conversion completed:\n\nInput: {{input}}\nOutput: {{output}}\nFormat: {{from_format}} → {{to_format}}\n\nVerification:\n{{verify_output.output}}"
    depends_on: [check_input]
    inputs: [verify_output.output]
---

# File Converter

Convert files between different formats.

## Usage

```bash
/file_converter input.md output.html
```

With parameters:
```
input=./readme.md
output=./readme.html
from_format=md
to_format=html
```

## Supported Conversions

- **md → html**: Markdown to HTML
- **json → csv**: JSON array to CSV
- **csv → json**: CSV to JSON
- **yaml → json**: YAML to JSON
- **docx → txt**: Word document to plain text
- **any → txt**: Generic text extraction (macOS)

## Examples

### Markdown to HTML
```
input=readme.md
output=readme.html
from_format=md
to_format=html
```

### JSON to CSV
```
input=data.json
output=data.csv
from_format=json
to_format=csv
```

### YAML to JSON
```
input=config.yaml
output=config.json
from_format=yaml
to_format=json
```

## Error Handling

- **Missing Python module:** Suggests installing required modules
- **Unsupported conversion:** Suggests alternative paths
- **File not found:** Validates input exists before attempting