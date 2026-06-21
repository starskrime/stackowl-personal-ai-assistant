---
name: document-extract
description: Use to pull specific structured content out of a document — tables, fields, lists, or other targeted data — beyond a plain summary.
when_to_use: When the user needs specific structured data extracted from a document (e.g. a table of figures, a list of named entities, specific fields from a form), not a general summary. For long-document summarisation, use the chunked-pdf-summary skill instead.
version: 0.1.0
tags: [document, extraction, pdf, parsing, structured-data]
author: stackowl-builtin
license: MIT
---

# Document Extraction

Summarisation collapses a document into prose. Extraction pulls specific
structured content — tables, named fields, lists, numerical values — out of
it verbatim. This skill enforces loading, locating, extracting, and
spot-checking the result so that hallucinated fields and silent truncation are
caught before they reach the user.

## Steps

1. **Load the document with `pdf` or `read_file`.** For PDF files, use the
   `pdf` tool. For plain-text, markdown, CSV, or other text formats, use
   `read_file`. Note the total page or line count so truncation can be
   detected in later steps.

2. **Locate the target sections.** Read the loaded content to identify which
   pages, sections, or segments contain the data the user wants. If the
   document is long and the tool returns paginated results, iterate through
   pages until the target section is found. Do not assume the first page
   contains the relevant content.

3. **Extract the target content.** For simple cases (a visible table, a
   labelled field), copy the relevant text verbatim. For complex cases
   (multi-column tables, nested structures, tabular data that needs
   reshaping), use `execute_code` to parse the loaded text programmatically
   and assemble the structured result (e.g. as JSON or CSV).

4. **Assemble and return the extracted result.** Present the extracted content
   in the format most useful to the user (table, list, JSON object, etc.).
   Include page or section references so the user can locate each value in
   the source document.

## Verification

Before delivering the extracted result:

- **Spot-check extracted values against the source text.** Pick two or three
  extracted values at random and confirm they appear verbatim (or with only
  formatting normalisation) in the loaded document content. If a value cannot
  be traced to a source page, remove it — do not guess.
- **Report coverage.** State how many pages or sections were searched and
  whether any could not be parsed (e.g. scanned images, password-protected
  pages, corrupted sections). Do not silently omit unparseable pages.
- **Do not invent fields.** Every field in the extracted output must have a
  corresponding source location. If a requested field is not present in the
  document, say so explicitly rather than substituting a plausible-sounding
  value.

## Pitfalls

- **Hallucinating fields not in the document.** The most common failure mode:
  the model "fills in" a field it expects to see but that is absent from the
  source. The spot-check step exists specifically to catch this — treat any
  value that cannot be traced to a source page as suspect.
- **Silent truncation of long documents.** If the `pdf` or `read_file` tool
  returns a page count lower than expected, or if the tool result is
  suspiciously short, the document may have been truncated. Note the
  truncation and iterate through remaining pages before reporting the result
  as complete.
- **OCR gaps unreported.** Scanned PDFs may contain image-only pages that the
  `pdf` tool cannot parse. If a page returns no text content, note it as an
  image-only or unreadable page rather than silently skipping it.
- **Confusing extraction with summarisation.** Extraction is verbatim or
  near-verbatim retrieval of specific content. If the user wants a general
  summary of a long document, use the `chunked-pdf-summary` skill instead.
- **Missing page references.** Extracted values without source references are
  unverifiable by the user. Always include page numbers or section identifiers
  alongside extracted content.
