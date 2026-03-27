---
name: math_solver
description: Solve mathematical problems including arithmetic, algebra, calculus, and unit conversions
openclaw:
  emoji: "🧮"
---

# Math Solver

Solve mathematical problems.

## Steps

1. **Parse the problem** from user input.
2. **Compute using Python:**
   ```bash
   run_shell_command("python3 -c 'print(<expression>)'")
   ```
   For complex math:
   ```bash
   run_shell_command("python3 -c 'import math; print(<expression>)'")
   ```
3. **Show step-by-step solution** for educational value.

## Examples

### Arithmetic

```bash
run_shell_command("python3 -c 'print(15 * 23 + 47 / 3)'")
```

### Trigonometry

```bash
run_shell_command("python3 -c 'import math; print(math.sin(math.radians(45)))'")
```

### Unit conversion

```bash
run_shell_command("python3 -c 'miles=26.2; print(f\"{miles} miles = {miles * 1.60934:.2f} km\")'")
```

## Error Handling

- **Invalid expression:** Show syntax error and suggest corrections.
- **Division by zero:** Catch and explain why it's undefined.
