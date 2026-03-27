/**
 * StackOwl — Lightweight Tool Argument Validator
 *
 * Validates tool call arguments against JSON Schema definitions.
 * No external dependencies — handles the subset of JSON Schema
 * used by tool definitions (type: object with simple property types).
 */

export function validateToolArgs(
  schema: Record<string, unknown> | undefined,
  args: Record<string, unknown>,
): string[] {
  if (!schema) return [];
  const violations: string[] = [];

  const properties = (schema.properties ?? {}) as Record<
    string,
    Record<string, unknown>
  >;
  const required = (schema.required ?? []) as string[];

  // Check required fields
  for (const field of required) {
    if (args[field] === undefined || args[field] === null) {
      violations.push(`Missing required field: "${field}"`);
    }
  }

  // Check types for provided fields
  for (const [key, value] of Object.entries(args)) {
    const propSchema = properties[key];
    if (!propSchema) continue; // allow extra fields

    const expectedType = propSchema.type as string | undefined;
    if (!expectedType) continue;

    const actualType = Array.isArray(value) ? "array" : typeof value;

    if (expectedType === "integer") {
      if (typeof value !== "number" || !Number.isInteger(value)) {
        violations.push(`Field "${key}" expected integer, got ${actualType}`);
      }
    } else if (expectedType === "number") {
      if (typeof value !== "number") {
        violations.push(`Field "${key}" expected number, got ${actualType}`);
      }
    } else if (expectedType !== actualType) {
      violations.push(
        `Field "${key}" expected ${expectedType}, got ${actualType}`,
      );
    }

    // Enum check
    const enumValues = propSchema.enum as unknown[] | undefined;
    if (enumValues && !enumValues.includes(value)) {
      violations.push(
        `Field "${key}" must be one of: ${enumValues.join(", ")}`,
      );
    }
  }

  return violations;
}
