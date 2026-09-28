// Numbers in a JSON text that the pipeline would not store as written (ADR 0002, "Numbers").
//
// Every hash in the system is over the JSON value, and a JSON number is an IEEE 754 double
// (RFC 8785, I-JSON): parsed, `2.50` is `2.5`, `1.0` is `1`, `1e2` is `100`, and a digit beyond a
// double's precision is gone. What the pipeline persists is the value, re-serialised, so a FHIR
// decimal's written precision (`2.50` has three significant figures, `2.5` two) would be dropped
// without anyone having decided to drop it. A document part whose every number is already in the
// form JavaScript writes (JSON.stringify, RFC 8785 section 3.2.2.3) loses nothing; any other is
// refused before it is hashed.
//
// The text must already be valid JSON (JSON.parse accepted it): only then is every run of number
// characters outside a string one number token. The scan is linear and holds no stack.

const NUMBER_CHARACTER = /[-+0-9.eE]/;

// Whether any number token's written form is not the one JavaScript writes for its value (`-0`
// included, which it writes `0`). A boolean, never the token: a caller reports the fact, not the
// text, which may sit inside document content.
export function hasNonCanonicalNumber(text: string): boolean {
  let inString = false;
  for (let index = 0; index < text.length; index += 1) {
    const character = text.charAt(index);
    if (inString) {
      if (character === "\\") index += 1;
      else if (character === '"') inString = false;
      continue;
    }
    if (character === '"') {
      inString = true;
      continue;
    }
    if (character !== "-" && (character < "0" || character > "9")) continue;
    let end = index + 1;
    while (end < text.length && NUMBER_CHARACTER.test(text.charAt(end))) end += 1;
    const token = text.slice(index, end);
    if (JSON.stringify(Number(token)) !== token) return true;
    index = end - 1;
  }
  return false;
}
