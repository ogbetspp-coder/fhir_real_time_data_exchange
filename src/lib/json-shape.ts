// Structural bounds on untrusted JSON. They are checked iteratively, before anything recursive
// (canonical hashing, the contract walk) touches a value, so that a pathological document is a
// classified rejection rather than a RangeError from a blown stack.

export const MAX_JSON_DEPTH = 48;
export const MAX_JSON_NODES = 200_000;

export function jsonShapeIssues(name: string, value: unknown): string[] {
  const stack: { value: unknown; depth: number }[] = [{ value, depth: 0 }];
  let nodes = 1;

  while (stack.length > 0) {
    const item = stack.pop();
    if (item === undefined) break;
    if (item.depth > MAX_JSON_DEPTH) return [`${name} nesting exceeds depth ${MAX_JSON_DEPTH}`];

    const current = item.value;
    let children: unknown[];
    if (Array.isArray(current)) {
      children = current;
    } else if (current !== null && typeof current === "object") {
      children = Object.values(current as Record<string, unknown>);
    } else {
      continue;
    }

    // The budget is consulted before a node's children reach the work stack, never after. A
    // single wide array would otherwise be expanded in full — one stack entry per element,
    // gigabytes for a document well inside the size cap — to arrive at the rejection the node
    // count was supposed to reach first. Arrays are counted without being copied.
    if (nodes + children.length > MAX_JSON_NODES) {
      return [`${name} exceeds ${MAX_JSON_NODES} JSON nodes`];
    }
    nodes += children.length;
    for (const child of children) stack.push({ value: child, depth: item.depth + 1 });
  }

  return [];
}
