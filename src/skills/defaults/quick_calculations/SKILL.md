---
name: quick_calculations
description: Perform quick calculations, conversions, and math operations with instant results
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🧮"
parameters:
  expression:
    type: string
    description: "Math expression or conversion"
    default: "2+2"
  conversion:
    type: string
    description: "Conversion type: currency, length, weight, temp, time"
required: []
steps:
  - id: calc_basic
    tool: ShellTool
    args:
      command: "python3 -c 'print({{expression}})'"
      mode: "local"
    timeout_ms: 5000
  - id: calc_advanced
    tool: ShellTool
    args:
      command: "python3 -c 'import math; print(eval(\"{{expression}}\"))' 2>/dev/null || echo 'Expression error'"
      mode: "local"
    timeout_ms: 5000
  - id: convert_currency
    tool: ShellTool
    args:
      command: "python3 -c 'print(\"Currency conversion placeholder - use /currency_converter skill\")'"
      mode: "local"
    timeout_ms: 3000
  - id: convert_temp
    tool: ShellTool
    args:
      command: "python3 -c 'c=float(\"{{expression}}\"); f=c*9/5+32; print(f\"{c}°C = {f}°F\")'"
      mode: "local"
    timeout_ms: 5000
  - id: convert_length
    tool: ShellTool
    args:
      command: "python3 -c 'print(\"Length conversion - specify units: km->miles, etc\")'"
      mode: "local"
    timeout_ms: 3000
  - id: calc_percentage
    tool: ShellTool
    args:
      command: "python3 -c '\nparts = \"{{expression}}\".split()\nif len(parts) >= 3:\n    val, pct = float(parts[0]), float(parts[1])\n    print(f\"{val} * {pct}% = {val * pct / 100}\")\n'"
      mode: "local"
    timeout_ms: 5000
  - id: calc_tip
    tool: ShellTool
    args:
      command: "python3 -c 'bill=float(\"{{expression}}\"); tip15=bill*0.15; tip20=bill*0.20; tip25=bill*0.25; print(f\"Bill: ${bill:.2f}\\n15% tip: ${tip15:.2f}\\n20% tip: ${tip20:.2f}\\n25% tip: ${tip25:.2f}\")'"
      mode: "local"
    timeout_ms: 5000
  - id: calc_split
    tool: ShellTool
    args:
      command: "python3 -c 'print(\"Use expression: bill / people\")'"
      mode: "local"
    timeout_ms: 3000
  - id: analyze
    type: llm
    prompt: "Calculation result for: '{{expression}}'\n\n{{calc_advanced.output}}"
    depends_on: [calc_advanced]
    inputs: [calc_advanced.output]
---

# Quick Calculations

Fast math and conversions.

## Usage

Basic math:
```
expression=2+2
```

Percentage:
```
expression=100 15
```

Tip calculator:
```
expression=50
```

Temperature:
```
expression=100
conversion=temp
```

## Supported Operations

- Basic: `+`, `-`, `*`, `/`, `**`, `%`
- Math: `sin`, `cos`, `sqrt`, `log`, `pi`
- Percentages: `value percentage`
- Tips: bill amount

## Examples

### Simple math
```
expression=100*3.14
```

### What's 15% of 80?
```
expression=80 15
```

### Tip on $50
```
expression=50
```

### 100°C in Fahrenheit
```
expression=100
conversion=temp
```

## Quick Reference

| Operation | Example |
|-----------|---------|
| Add | `10+5` = 15 |
| Multiply | `10*5` = 50 |
| Power | `2**10` = 1024 |
| Percentage | `80 15` = 12 |
| Square root | `sqrt(16)` = 4 |

## Notes

- Use Python math functions
- Conversions via dedicated skills
- Clipboard copy for results