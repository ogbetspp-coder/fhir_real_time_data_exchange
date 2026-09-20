// Structural bounds on untrusted JSON. They are checked iteratively, before anything recursive
// (canonical hashing, the contract walk) touches a value, so that a pathological document is a
// classified rejection rather than a RangeError from a blown stack.

export const MAX_JSON_DEPTH = 48;
export const MAX_JSON_NODES = 200_000;

export function jsonShapeIssues(name: string, value: unknown): string[] {
  const stack: { value: unknown; depth: number }[] = [{ value, depth: 0 }];
  let nodes = 0;
  while (stack.length > 0) {
    const item = stack.pop();
    if (item === undefined) break;
    nodes += 1;
    if (nodes > MAX_JSON_NODES) return [`${name} exceeds ${MAX_JSON_NODES} JSON nodes`];
    if (item.depth > MAX_JSON_DEPTH) return [`${name} nesting exceeds depth ${MAX_JSON_DEPTH}`];
    const current = item.value;
    if (Array.isArray(current)) {
      for (const child of current) stack.push({ value: child, depth: item.depth + 1 });
    } else if (current !== null && typeof current === "object") {
      for (const child of Object.values(current as Record<string, unknown>)) {
        stack.push({ value: child, depth: item.depth + 1 });
      }
    }
  }
  return [];
}
