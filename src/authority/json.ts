// The authority's bytes, read without guessing (docs/design/authority-import-contract.md, D1):
// strict UTF-8 (an invalid byte or a byte-order mark refuses) and JSON with no duplicate key
// (JSON.parse keeps the last of two silently, so the same bytes could be read two ways).

export class AuthorityBytesError extends Error {
  public constructor(public readonly reason: string) {
    super(`Authority bytes refused: ${reason}`);
    this.name = "AuthorityBytesError";
  }
}

const DECODER = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true });

export function decodeUtf8(bytes: Uint8Array): string {
  if (bytes[0] === 0xef && bytes[1] === 0xbb && bytes[2] === 0xbf) {
    throw new AuthorityBytesError("byte-order-mark");
  }
  try {
    return DECODER.decode(bytes);
  } catch {
    throw new AuthorityBytesError("invalid-utf-8");
  }
}

export type Json = null | boolean | number | string | Json[] | { [key: string]: Json };

// Deep enough for any FHIR ePI the authority serves, shallow enough never to exhaust the stack.
const MAX_DEPTH = 128;

// A strict RFC 8259 parser that refuses a duplicate key in any object.
export function parseStrictJson(text: string): Json {
  let at = 0;
  const fail = (reason: string): never => {
    throw new AuthorityBytesError(reason);
  };
  const whitespace = (): void => {
    while (at < text.length && " \t\n\r".includes(text.charAt(at))) at += 1;
  };
  const literal = (word: string, value: Json): Json => {
    if (text.startsWith(word, at)) {
      at += word.length;
      return value;
    }
    return fail("invalid-json");
  };
  const string = (): string => {
    at += 1; // the opening quote
    let out = "";
    for (;;) {
      if (at >= text.length) fail("invalid-json");
      const character = text.charAt(at);
      if (character === '"') {
        at += 1;
        return out;
      }
      if (character.charCodeAt(0) < 0x20) fail("invalid-json");
      if (character !== "\\") {
        out += character;
        at += 1;
        continue;
      }
      const escape = text.charAt(at + 1);
      const simple: Record<string, string> = {
        '"': '"',
        "\\": "\\",
        "/": "/",
        b: "\b",
        f: "\f",
        n: "\n",
        r: "\r",
        t: "\t",
      };
      if (escape in simple) {
        out += simple[escape] ?? "";
        at += 2;
      } else if (escape === "u") {
        const hex = text.slice(at + 2, at + 6);
        if (!/^[0-9a-fA-F]{4}$/.test(hex)) fail("invalid-json");
        out += String.fromCharCode(Number.parseInt(hex, 16));
        at += 6;
      } else {
        fail("invalid-json");
      }
    }
  };
  const number = (): number => {
    const match = /^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/.exec(text.slice(at));
    if (match === null) return fail("invalid-json");
    // Only integers in their one spelling: the authority's files hold none but its enumeration
    // codes, and "-0", "0.0" or "0e0" would read as the same 0 from other bytes.
    if (!/^(?:0|-?[1-9]\d{0,14})$/.test(match[0])) return fail("non-canonical-number");
    at += match[0].length;
    return Number(match[0]);
  };
  const value = (depth: number): Json => {
    if (depth > MAX_DEPTH) fail("json-too-deep");
    whitespace();
    const character = text.charAt(at);
    if (character === "{") {
      at += 1;
      const object: Record<string, Json> = {};
      const keys = new Set<string>();
      whitespace();
      if (text.charAt(at) === "}") {
        at += 1;
        return object;
      }
      for (;;) {
        whitespace();
        if (text.charAt(at) !== '"') fail("invalid-json");
        const key = string();
        if (keys.has(key)) fail("duplicate-json-key");
        keys.add(key);
        whitespace();
        if (text.charAt(at) !== ":") fail("invalid-json");
        at += 1;
        Object.defineProperty(object, key, {
          value: value(depth + 1),
          enumerable: true,
          writable: true,
          configurable: true,
        });
        whitespace();
        if (text.charAt(at) === ",") {
          at += 1;
          continue;
        }
        if (text.charAt(at) === "}") {
          at += 1;
          return object;
        }
        fail("invalid-json");
      }
    }
    if (character === "[") {
      at += 1;
      const array: Json[] = [];
      whitespace();
      if (text.charAt(at) === "]") {
        at += 1;
        return array;
      }
      for (;;) {
        array.push(value(depth + 1));
        whitespace();
        if (text.charAt(at) === ",") {
          at += 1;
          continue;
        }
        if (text.charAt(at) === "]") {
          at += 1;
          return array;
        }
        fail("invalid-json");
      }
    }
    if (character === '"') return string();
    if (character === "t") return literal("true", true);
    if (character === "f") return literal("false", false);
    if (character === "n") return literal("null", null);
    return number();
  };
  const result = value(0);
  whitespace();
  if (at !== text.length) fail("invalid-json");
  return result;
}

// The authority's file, decoded and parsed.
export function readAuthorityJson(bytes: Uint8Array): Json {
  return parseStrictJson(decodeUtf8(bytes));
}
