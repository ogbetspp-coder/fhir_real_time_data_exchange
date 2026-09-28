import { describe, expect, it } from "vitest";

import { crc32c } from "../src/lib/crc32c.js";

describe("crc32c", () => {
  // The CRC-32C check value (RFC 3720, appendix B.4) and the empty input.
  it("matches the standard check values", () => {
    expect(crc32c(Buffer.from("123456789"))).toBe(0xe3069283);
    expect(crc32c(new Uint8Array(0))).toBe(0);
    expect(crc32c(new Uint8Array(32))).toBe(0x8a9136aa);
  });
});
