---
name: pdf_extract
description: Extract text content from PDF files for reading, summarizing, or processing
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "📄"
parameters:
  file_path:
    type: string
    description: "Path to the PDF file to extract text from"
  max_lines:
    type: number
    description: "Maximum number of lines to extract (0 for all)"
    default: 100
required: [file_path]
steps:
  - id: check_tools
    tool: ShellTool
    args:
      command: "which pdftotext || which textutil || echo 'NO_PDF_TOOL'"
      mode: "local"
  - id: extract_pdftotext
    tool: ShellTool
    args:
      command: "pdftotext {{file_path}} - {{if(max_lines > 0, '| head -n ' + max_lines, '')}}"
      mode: "local"
    optional: true
    depends_on: [check_tools]
  - id: extract_textutil
    tool: ShellTool
    args:
      command: "textutil -convert txt -stdout {{file_path}} {{if(max_lines > 0, '| head -n ' + max_lines, '')}}"
      mode: "local"
    optional: true
    depends_on: [check_tools]
  - id: present_results
    type: llm
    prompt: "Present the extracted PDF text content from the extraction step. If both extraction methods failed, explain that pdftotext or textutil needs to be installed."
    depends_on: [extract_pdftotext, extract_textutil]
    inputs: [extract_pdftotext.output, extract_textutil.output]
---

# PDF Text Extraction

Extract text from PDF files.

## Usage

```bash
/pdf_extract file_path=./report.pdf
/pdf_extract file_path=./report.pdf max_lines=50
```

## Parameters

- **file_path**: Path to the PDF file to extract text from (required)
- **max_lines**: Maximum number of lines to extract (default: 100, use 0 for all)

## Examples

### Extract from a PDF

```bash
pdftotext report.pdf - | head -100
```

## Error Handling

- **No tools available:** Install `brew install poppler` for pdftotext.
- **Scanned PDF (image-based):** Note that OCR is needed; suggest using `tesseract`.
- **Encrypted PDF:** Inform user that the PDF is password-protected.
