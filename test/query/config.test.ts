import { describe, expect, it } from "vitest";

import { loadQueryConfig } from "../../src/query/config.js";
import { parseEntitlements } from "../../src/query/entitlements.js";

// The query service's own configuration (src/query/config.ts) and its entitlement map
// (src/query/entitlements.ts). Both are parsed once at startup, so every rule here is a rule
// about what the service refuses to start with, and what it assumes when a variable is unset.

const REQUIRED = {
  GOOGLE_CLOUD_PROJECT: "synthetic-project",
  HEALTHCARE_DATASET_ID: "ema-flow",
  TARGET_FHIR_STORE_ID: "validated",
  QUERY_AUDIENCE: "https://query.ema-flow.invalid",
};

function load(extra: Record<string, string | undefined> = {}) {
  return loadQueryConfig({ ...REQUIRED, ...extra });
}

describe("the query service configuration", () => {
  it("applies its defaults when only the required variables are set", () => {
    const config = load();
    expect(config).toMatchObject({
      GCP_LOCATION: "europe-west4",
      QUERY_LOG_REJECTION_REASON: false,
      QUERY_OAUTH_CLIENT_IDS: [],
      QUERY_ENTITLEMENTS_JSON: "{}",
      QUERY_SERVICE_VERSION: "development",
      PORT: 8080,
    });
    expect(config.IMAGE_DIGEST).toBeUndefined();
  });

  it.each([
    "GOOGLE_CLOUD_PROJECT",
    "HEALTHCARE_DATASET_ID",
    "TARGET_FHIR_STORE_ID",
    "QUERY_AUDIENCE",
  ])("refuses to start without %s, or with it blank", (name) => {
    expect(() => load({ [name]: undefined })).toThrow();
    expect(() => load({ [name]: "   " })).toThrow();
  });

  describe("logging the reason for an authentication refusal", () => {
    it("is off unless set", () => {
      expect(load().QUERY_LOG_REJECTION_REASON).toBe(false);
      expect(load({ QUERY_LOG_REJECTION_REASON: "false" }).QUERY_LOG_REJECTION_REASON).toBe(false);
    });

    it("is on only for the exact string true", () => {
      expect(load({ QUERY_LOG_REJECTION_REASON: "true" }).QUERY_LOG_REJECTION_REASON).toBe(true);
    });

    // Anything else is a configuration error, not a quiet `false` and never a quiet `true`.
    it.each(["TRUE", "1", "yes", "on", "", " true"])("refuses %j", (value) => {
      expect(() => load({ QUERY_LOG_REJECTION_REASON: value })).toThrow();
    });
  });

  describe("accepted OAuth client ids", () => {
    it("is empty when unset or empty, so every access token is refused", () => {
      expect(load().QUERY_OAUTH_CLIENT_IDS).toEqual([]);
      expect(load({ QUERY_OAUTH_CLIENT_IDS: "" }).QUERY_OAUTH_CLIENT_IDS).toEqual([]);
      expect(load({ QUERY_OAUTH_CLIENT_IDS: " , ,, " }).QUERY_OAUTH_CLIENT_IDS).toEqual([]);
    });

    it("splits on commas, trims each id and drops empty ones", () => {
      const ids = load({
        QUERY_OAUTH_CLIENT_IDS:
          " 123-abc.apps.googleusercontent.com ,,456-def.apps.googleusercontent.com, ",
      }).QUERY_OAUTH_CLIENT_IDS;
      expect(ids).toEqual([
        "123-abc.apps.googleusercontent.com",
        "456-def.apps.googleusercontent.com",
      ]);
    });

    it("accepts 64 ids and refuses the 65th", () => {
      const ids = (count: number) =>
        Array.from({ length: count }, (_, index) => `client-${String(index)}`).join(",");
      expect(load({ QUERY_OAUTH_CLIENT_IDS: ids(64) }).QUERY_OAUTH_CLIENT_IDS).toHaveLength(64);
      expect(() => load({ QUERY_OAUTH_CLIENT_IDS: ids(65) })).toThrow();
    });

    it("refuses a value longer than 8192 characters before splitting it", () => {
      const long = `a${"b".repeat(8_192)}`;
      expect(() => load({ QUERY_OAUTH_CLIENT_IDS: long })).toThrow();
    });

    // The grammar keeps whitespace, and anything that could be read as a second separator,
    // out of one id; a value that is not an id stops startup rather than being dropped.
    it.each([
      ["an embedded space", "abc def"],
      ["a semicolon", "abc;def"],
      ["a leading punctuation mark", "-abc"],
      ["an id of 257 characters", `a${"b".repeat(256)}`],
      ["a quote", 'abc"def'],
    ])("refuses an id with %s", (_label, id) => {
      expect(() => load({ QUERY_OAUTH_CLIENT_IDS: `good-id,${id}` })).toThrow();
    });
  });

  it("refuses an IMAGE_DIGEST that is not exactly sha256:<64 hex>", () => {
    const digest = `sha256:${"ab".repeat(32)}`;
    expect(load({ IMAGE_DIGEST: digest }).IMAGE_DIGEST).toBe(digest);
    expect(() => load({ IMAGE_DIGEST: `sha256:${"ab".repeat(31)}` })).toThrow();
    expect(() => load({ IMAGE_DIGEST: `example.com/image@${digest}` })).toThrow();
  });

  it("refuses a PORT outside 1-65535, and coerces a numeric string", () => {
    expect(load({ PORT: "9090" }).PORT).toBe(9090);
    expect(() => load({ PORT: "0" })).toThrow();
    expect(() => load({ PORT: "65536" })).toThrow();
    expect(() => load({ PORT: "http" })).toThrow();
  });

  it("refuses an entitlement map longer than a megabyte before it is parsed", () => {
    expect(() => load({ QUERY_ENTITLEMENTS_JSON: " ".repeat(1_000_001) })).toThrow();
  });
});

describe("the entitlement map", () => {
  it("answers each principal's own bundles, and nothing for a principal it does not name", () => {
    const directory = parseEntitlements(
      JSON.stringify({ "112233445566778899001": { bundles: ["synthetic-type2-smpc"] } }),
    );
    expect(directory.entitlementsFor("112233445566778899001")).toEqual({
      bundles: ["synthetic-type2-smpc"],
    });
    expect(directory.entitlementsFor("998877665544332211000")).toBeUndefined();
  });

  it("is empty for the default {}", () => {
    expect(parseEntitlements("{}").entitlementsFor("112233445566778899001")).toBeUndefined();
  });

  // The value holds principal identifiers, so the error names the variable and says no more.
  it.each(["{", "not json", "", "{'a': 1}"])(
    "refuses invalid JSON %j without quoting it",
    (json) => {
      let thrown: unknown;
      try {
        parseEntitlements(json);
      } catch (error) {
        thrown = error;
      }
      expect(thrown).toBeInstanceOf(Error);
      expect((thrown as Error).message).toBe("QUERY_ENTITLEMENTS_JSON is not valid JSON");
    },
  );

  it.each([
    ["a top-level array", []],
    ["a top-level string", "principal"],
    ["null", null],
    ["a principal whose entry is not an object", { "112233445566778899001": ["bundle"] }],
    ["an entry with no bundles", { "112233445566778899001": {} }],
    [
      "an entry with a key the schema does not know",
      { "112233445566778899001": { bundles: [], organisation: "x" } },
    ],
    ["a bundle id that is not a FHIR id", { "112233445566778899001": { bundles: ["has space"] } }],
    ["a principal keyed by e-mail address", { "someone@example.com": { bundles: [] } }],
  ])("refuses %s, counting the issues but not quoting them", (_label, value) => {
    let thrown: unknown;
    try {
      parseEntitlements(JSON.stringify(value));
    } catch (error) {
      thrown = error;
    }
    expect(thrown).toBeInstanceOf(Error);
    const message = (thrown as Error).message;
    expect(message).toMatch(
      /^QUERY_ENTITLEMENTS_JSON is not a valid entitlement map \(\d+ issues\)$/,
    );
    expect(message).not.toContain("112233445566778899001");
    expect(message).not.toContain("someone@example.com");
  });
});
