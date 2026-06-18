// src/tools/tool-error.ts

export interface ToolErrorEnvelope {
  success: false;
  data: null;
  error: {
    code: string;
    message: string;
    suggestion?: string;
  };
}

export interface ToolSuccessEnvelope<T> {
  success: true;
  data: T;
}

/**
 * Return a JSON-serialized structured error envelope.
 * Tools return this string instead of throwing.
 *
 * Example: return toolError("FILE_NOT_FOUND", `Cannot read: ${path}`, "Check that the file exists");
 */
export function toolError(
  code: string,
  message: string,
  suggestion?: string,
): string {
  const envelope: ToolErrorEnvelope = {
    success: false,
    data: null,
    error: suggestion ? { code, message, suggestion } : { code, message },
  };
  return JSON.stringify(envelope);
}

/**
 * Return a JSON-serialized structured success envelope.
 * Tools return this string to give the LLM a consistently shaped result.
 *
 * Example: return toolSuccess({ rows: result, count: result.length });
 */
export function toolSuccess<T>(data: T): string {
  const envelope: ToolSuccessEnvelope<T> = { success: true, data };
  return JSON.stringify(envelope);
}
