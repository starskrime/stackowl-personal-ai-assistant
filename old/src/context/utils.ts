export function resolveUserId(userId?: string, sessionId?: string): string {
  return userId ?? sessionId ?? "anonymous";
}

export function hash(input: string): string {
  let h = 5381;
  for (let i = 0; i < input.length; i++) {
    h = ((h << 5) + h) ^ input.charCodeAt(i);
  }
  return (h >>> 0).toString(36);
}
