---
name: pdf_extract
description: Extract text content from PDF files for reading, summarizing, or processing
openclaw:
  emoji: "📄"
---

# PDF Text Extraction

Extract text from PDF files.

## Steps

1. **Check for extraction tools:**
   ```bash
   run_shell_command("which pdftotext || which textutil")
   ```
2. **Extract text:**
   - Using pdftotext: `run_shell_command("pdftotext <file.pdf> -")`
   - Using textutil (macOS): `run_shell_command("textutil -convert txt -stdout <file.pdf>")`
   - Using Python: `run_shell_command("python3 -c \"import subprocess; subprocess.run(['textutil', '-convert', 'txt', '-stdout', '<file.pdf>'])\"")`
3. **Present extracted text** or save to file.

## Examples

### Extract from a PDF

```bash
run_shell_command("pdftotext report.pdf - | head -100")
```

## Error Handling

- **No tools available:** Install `brew install poppler` for pdftotext.
- **Scanned PDF (image-based):** Note that OCR is needed; suggest using `tesseract`.
- **Encrypted PDF:** Inform user that the PDF is password-protected.
