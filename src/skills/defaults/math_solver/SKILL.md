---
name: math_solver
description: Solve mathematical problems including arithmetic, algebra, calculus, and unit conversions
command-dispatch: tool
command-tool: ShellTool
openclaw:
  emoji: "🧮"
parameters:
  expression:
    type: string
    description: "Mathematical expression to solve"
  type:
    type: string
    description: "Type: arithmetic, algebra, calculus, or conversion"
    default: "arithmetic"
required: [expression]
steps:
  - id: compute
    tool: ShellTool
    args:
      command: "python3 -c 'print({{expression}})'"
      mode: "local"
    timeout_ms: 10000
  - id: compute_math
    tool: ShellTool
    args:
      command: "python3 -c 'import math; print({{expression}})'"
      mode: "local"
    timeout_ms: 10000
    optional: true
  - id: convert
    tool: ShellTool
    args:
      command: "python3 -c '{{conversion}}'"
      mode: "local"
    timeout_ms: 10000
    optional: true
---

# Math Solver

Solve mathematical problems.

## Usage

```bash
/math_solver expression=<expr> type=<type>
```

## Parameters

- **expression**: Mathematical expression to solve
- **type**: Type: arithmetic, algebra, calculus, or conversion (default: arithmetic)

## Examples

### Arithmetic

```
expression=15 * 23 + 47 / 3
type=arithmetic
```

### Trigonometry

```
expression=math.sin(math.radians(45))
type=calculus
```

### Unit conversion

```
expression=miles=26.2; print(f"{miles} miles = {miles * 1.60934:.2f} km")
type=conversion
```

## Error Handling

- **Invalid expression:** Show syntax error and suggest corrections.
- **Division by zero:** Catch and explain why it's undefined.
