---
name: generate_docs
description: Generate API documentation or code documentation from source files with JSDoc or docstring analysis
openclaw:
  emoji: "📖"
---

# Generate Documentation

Create documentation from source code.

## Steps

1. **Read the source file:**
   ```bash
   read_file("<source_file>")
   ```
2. **Extract functions/classes** and their signatures, parameters, return types.
3. **Generate markdown documentation:**
   - Function/method name
   - Parameters with types and descriptions
   - Return value
   - Usage examples
4. **Save documentation:**
   ```bash
   write_file("docs/<module_name>.md", "<generated_docs>")
   ```

## Examples

### Document a TypeScript module

```bash
read_file("src/skills/parser.ts")
write_file("docs/parser.md", "<documentation>")
```

## Error Handling

- **No type annotations:** Note "Types inferred from usage" and document best-effort.
- **Binary file:** Skip with "Cannot generate docs for binary files."
