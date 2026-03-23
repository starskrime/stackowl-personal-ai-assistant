---
name: file_converter
description: Convert files between formats including markdown to HTML, JSON to CSV, and document conversions
openclaw:
  emoji: "🔄"
---
# File Converter
Convert files between different formats.
## Steps
1. **Identify source and target formats.**
2. **Convert using appropriate tool:**
   - **MD → HTML:** `run_shell_command("python3 -c \"import markdown; print(markdown.markdown(open('<file>').read()))\" > output.html")`
   - **JSON → CSV:** `run_shell_command("python3 -c \"import json,csv,sys; data=json.load(open('<file>')); w=csv.DictWriter(sys.stdout,data[0].keys()); w.writeheader(); w.writerows(data)\" > output.csv")`
   - **DOC → TXT (macOS):** `run_shell_command("textutil -convert txt <file.docx>")`
   - **YAML → JSON:** `run_shell_command("python3 -c \"import yaml,json; print(json.dumps(yaml.safe_load(open('<file>')),indent=2))\"")` 
3. **Confirm conversion** and present output file path.
## Examples
### Convert DOCX to TXT
```bash
run_shell_command("textutil -convert txt report.docx")
```
## Error Handling
- **Missing Python module:** Install with `pip3 install <module>`.
- **Unsupported format pair:** Suggest alternative conversion paths.
